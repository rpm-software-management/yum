

import os
import fnmatch
import time
from datetime import datetime

import subprocess
from yum import _

try:
    import lvm

    #  Check that lvm2 is at least 2.2.99... In theory hacked versions of
    # .98 work, but meh.

    _ver = lvm.getVersion()
    # Looks liks: 2.02.84(2) (2011-02-09)
    _ver = _ver.split()[0]
    _ver = _ver.split('(')[0]
    _ver = tuple(map(int, _ver.split('.')))
    if _ver < (2, 2, 99):
        lvm = None
except:
    lvm = None
    _ver = None

if lvm is not None:
    from lvm import LibLVMError
    class _ResultError(LibLVMError):
        """Exception raised for LVM calls resulting in bad return values."""
        pass
else:
    LibLVMError = None


def _is_origin(lv):
    snap = lv.getAttr()
    # snap=(<value>, <is settable>)
    if not snap[0]: # Broken??
        return None
    return snap[0][0] in ('o', 'O')

def _is_snap(lv):
    snap = lv.getAttr()
    # snap=(<value>, <is settable>)
    if not snap[0]: # Broken??
        return None
    return snap[0][0] in ('s', 'S')

def _is_virt(lv):
    snap = lv.getAttr()
    # snap=(<value>, <is settable>)
    if not snap[0]: # Broken??
        return None
    return snap[0][0] == 'v'

def _vg_name2lv(vg, lvname):
    try:
        return vg.lvFromName(lvname)
    except:
        return None

def _list_vg_names():
    try:
        names = lvm.listVgNames()
    except LibLVMError:
        # Try to use the lvm binary instead
        names = []

    if not names: # Could be just broken...
        p = subprocess.Popen(["/sbin/lvm", "vgs", "-o", "vg_name"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        err = p.wait()
        if err:
            raise _ResultError(_("Failed to obtain volume group names"))

        output = p.communicate()[0]
        output = output.split('\n')
        if not output:
            return []
        header = output[0].strip()
        if header != 'VG':
            return []
        names = []
        for name in output[1:]:
            if not name:
                break
            names.append(name.strip())

    return names

def _z_off(z, ctime=0):
    if len(z) == 5: # +0000 / -0130 / etc.
        off = int(z[1:3]) * 60
        off += int(z[3:5])
        off *= 60
        if z[0] == '+':
            ctime -= off
        if z[0] == '-':
            ctime += off
    return ctime

def _lv_ctime2utc(ctime):
    try: # Welcome to insanity ...
        d,t,z = ctime.split()
        ctime = time.strptime(d + ' ' + t, "%Y-%m-%d %H:%M:%S")

        ctime = time.mktime(ctime)

        if False: # Ignore the offset atm. ... we using this to delete older.
            cur_z = time.strftime("%z")
            if cur_z != z: # lol ...
                cur_z = _z_off(cur_z)
                z = _z_off(z)
                ctime += (cur_z - z)

    except:
        ctime = 0

    return ctime

def _lv_data(vg, lv):
    vgname = vg.getName()
    lvname = lv.getName()

    size = lv.getSize()
    origin = lv.getProperty("origin")[0]
    tags = lv.getTags()

    ctime = _lv_ctime2utc(lv.getProperty("lv_time")[0])

    used = lv.getProperty("snap_percent")[0]
    used = float(used)
    used = used / (1 * 1000 * 1000)

    data = {'dev' : "%s/%s" % (vgname, lvname),
            'ctime' : ctime,
            'origin' : origin,
            'origin_dev' : "%s/%s" % (vgname, origin),
            'free' : vg.getFreeSize(),
            'tags' : tags,
            'size' : size,
            'used' : used}

    return data

def _log_traceback(func):
    """Decorator for _FSSnap methods that logs LVM tracebacks."""
    def wrap(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except LibLVMError as e:
            if self._logger is not None:
                self._logger.exception(e)
            raise
    return wrap

def lvmerr2str(exc):
    """Convert a LibLVMError instance to a readable error message."""
    if type(exc) == LibLVMError and len(exc.args) == 2:
        # args[0] is the error number so ignore that
        return exc.args[1]
    else:
        return str(exc)


class _FSSnap(object):

    # Old style was: vg/lv_root vg/lv_swap
    # New style is: fedora/root fedora/swap
    # New style is: redhat/root redhat/swap
    def __init__(self, root="/", lookup_mounts=True,
                 devices=('!*/swap', '!*/lv_swap'), logger=None):
        if not lvm or os.geteuid():
            devices = []

        self.version = _ver
        # Parts of the API seem to work even when lvm is not actually installed, hence the path test
        self.available = bool(lvm and os.path.exists("/sbin/lvm"))
        self.postfix_static = "_yum_"
        self._postfix = None
        self._root = root
        self._devs = devices
        self._vgname_list = None
        # Logger object to be used for LVM traceback logging
        self._logger = logger

        if not self._devs:
            return

    @property
    def _vgnames(self):
        if self._vgname_list is None:
            self._vgname_list = _list_vg_names() if self.available else []
        return self._vgname_list

    def _use_dev(self, vgname, lv=None):

        if lv is not None:
            if _is_snap(lv) or _is_virt(lv): # Don't look at these.
                return False

        found_neg = False

        for dev in self._devs:
            if '/' not in dev: # Bad...
                continue

            neg = False
            if dev[0] == '!':
                found_neg = True
                neg = True
                dev = dev[1:]

            vgn,lvn = dev.split('/', 1)
            if '/' in lvn:
                continue

            if not fnmatch.fnmatch(vgname, vgn):
                continue

            if lvn == '*':
                return not neg

            if lv is None:
                return None
            lvname = lv.getName()

            if not fnmatch.fnmatch(lvname, lvn):
                continue

            return not neg

        return found_neg

    @_log_traceback
    def has_space(self, percentage=100):
        """ See if we have enough space to try a snapshot. """

        ret = False
        for vgname in self._vgnames:
            use = self._use_dev(vgname)
            if use is not None and not use:
                continue

            vg = lvm.vgOpen(vgname, 'r')
            if not vg:
                raise _ResultError(
                    _("Unknown error when opening volume group ") + vgname)

            vgfsize = vg.getFreeSize()
            lvssize = 0

            for lv in vg.listLVs():
                if not self._use_dev(vgname, lv):
                    continue

                lvssize += lv.getSize()

            vg.close()

            if not lvssize:
                continue
            ret = True

            if (lvssize * percentage) > (100*vgfsize):
                return False

        return ret


    @_log_traceback
    def snapshot(self, percentage=100, prefix='', postfix=None, tags={}):
        """ Attempt to take a snapshot, note that errors can happen after
            this function succeeds. """

        if postfix is None:
            postfix = '%s%s' % (self.postfix_static, datetime.now().strftime("%Y%m%d%H%M%S.%f"))

        ret = []
        for vgname in self._vgnames:
            use = self._use_dev(vgname)
            if use is not None and not use:
                continue

            vg = lvm.vgOpen(vgname, 'w')
            if not vg:
                raise _ResultError(
                    _("Unknown error when opening volume group ") + vgname)

            for lv in vg.listLVs():
                lvname = lv.getName()

                if not self._use_dev(vgname, lv):
                    continue

                nlvname = "%s%s%s" % (prefix, lvname, postfix)
                nlv = lv.snapshot(nlvname, (lv.getSize() * percentage) / 100)
                if not nlv: # Failed here ... continuing seems bad.
                    vg.close()
                    raise _ResultError(
                        _("Unknown error when creating snapshot ") + nlvname)

                odev = "%s/%s" % (vgname,  lvname)
                ndev = "%s/%s" % (vgname, nlvname)

                # FIXME: yum_fssnapshot_pre_lv_name=<blah>
                eq_tags = set()
                for val in (ndev, odev, '*'):
                    for tag in tags.get(val, []):
                        if '=' in tag:
                            eq_tag_key,eq_tag_val = tag.split('=', 1)
                            if eq_tag_key in eq_tags:
                                continue
                            eq_tags.add(eq_tag_key)

                        nlv.addTag(tag)

                ret.append((odev, ndev))

            vg.close()

        return ret

    @_log_traceback
    def old_snapshots(self):
        """ List data for old snapshots. """

        ret = []
        for vgname in self._vgnames:
            #  We could filter out the VGs using _use_dev() but this way we'll
            # see stuff after changing config. options.

            vg = lvm.vgOpen(vgname, 'w')
            if not vg:
                raise _ResultError(
                    _("Unknown error when opening volume group ") + vgname)

            for lv in vg.listLVs():

                if not _is_snap(lv): # No snapshot means, we don't care.
                    continue

                ret.append(_lv_data(vg, lv))
            vg.close()

        return ret

    @_log_traceback
    def del_snapshots(self, devices=[]):
        """ Remove snapshots. """

        if not lvm:
            return []

        ret = []

        togo = {}
        for dev in devices:
            vgname,lvname = dev.split('/')
            if vgname not in togo:
                togo[vgname] = set([lvname])
            else:
                togo[vgname].add(lvname)

        for vgname in togo:
            vg = lvm.vgOpen(vgname, 'w')
            if not vg:
                raise _ResultError(
                    _("Unknown error when opening volume group ") + vgname)

            for lvname in togo[vgname]:
                lv = _vg_name2lv(vg, lvname)
                if not lv:
                    continue

                if not _is_snap(lv): # No snapshot means don't try to delete!
                    continue

                ret.append(_lv_data(vg, lv))

                lv.remove()

            vg.close()

        return ret
