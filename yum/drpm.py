#  Integrated delta rpm support
#  Copyright 2013 Zdenek Pavlas

#   This library is free software; you can redistribute it and/or
#   modify it under the terms of the GNU Lesser General Public
#   License as published by the Free Software Foundation; either
#   version 2.1 of the License, or (at your option) any later version.
#
#   This library is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#   Lesser General Public License for more details.
#
#   You should have received a copy of the GNU Lesser General Public
#   License along with this library; if not, write to the
#      Free Software Foundation, Inc.,
#      59 Temple Place, Suite 330,
#      Boston, MA  02111-1307  USA

from yum.constants import TS_UPDATE
from yum.Errors import RepoError
from yum.i18n import exception2msg, _
from yum.Errors import MiscError
from yum.misc import checksum, repo_gen_decompress, unlink_f
from urlgrabber import grabber, progress
async = hasattr(grabber, 'parallel_wait')
from xml.etree.cElementTree import iterparse
import os, re

APPLYDELTA = '/usr/bin/applydeltarpm'

class DeltaPackage:
    def __init__(self, rpm, size, remote, csum, oldrpm):
        # copy what needed
        self.rpm = rpm
        self.repo = rpm.repo
        self.basepath = rpm.basepath
        self.pkgtup = rpm.pkgtup
        (self.name, self.arch, self.epoch,
         self.version, self.release) = self.pkgtup
        self._hash = None

        # set up drpm attributes
        self.size = size
        self.relativepath = remote
        self.localpath = os.path.dirname(rpm.localpath) +'/'+ os.path.basename(remote)
        self.csum = csum
        self.oldrpm = oldrpm

    def __str__(self):
        return 'Delta RPM of %s' % self.rpm

    def __cmp__(self, other):
        if other is None:
            return 1

        #  Not a PackageObject(), so do this ourselves the bad way:
        return (cmp(self.name, other.name) or
                cmp(self.epoch, other.epoch) or
                cmp(self.version, other.version) or
                cmp(self.release, other.release) or
                cmp(self.arch, other.arch))

    def __hash__(self): # C&P from PackageObject...
        if self._hash is None:
            mystr = '%s - %s:%s-%s-%s.%s' % (self.repo.id, self.epoch, self.name,
                                         self.version, self.release, self.arch)
            self._hash = hash(mystr)
        return self._hash

    def localPkg(self):
        return self.localpath

    def getDiscNum(self):
        return None

    def verifyLocalPkg(self):
        # check file size first
        try: fsize = os.path.getsize(self.localpath)
        except OSError: return False
        if fsize != self.size: return False

        # checksum
        ctype, csum = self.csum
        try: fsum = checksum(ctype, self.localpath)
        except MiscError: return False
        if fsum != csum: return False

        # hooray
        return True

def _num_cpus_online(unknown=1):
    if not hasattr(os, "sysconf"):
        return unknown

    if not os.sysconf_names.has_key("SC_NPROCESSORS_ONLN"):
        return unknown

    ncpus = os.sysconf("SC_NPROCESSORS_ONLN")
    try:
        if int(ncpus) > 0:
            return ncpus
    except:
        pass

    return unknown

class DeltaInfo:
    def __init__(self, ayum, pkgs, adderror):
        self.verbose_logger = ayum.verbose_logger
        self.adderror = adderror
        self.jobs = {}
        self._future_jobs = []
        self.progress = None
        self.limit = ayum.conf.deltarpm
        if self.limit < 0:
            nprocs = _num_cpus_online()
            self.limit *= -nprocs

        if not self.limit: # Turned off.
            return

        # calculate update sizes
        oldrpms = {}
        pinfo = {}
        reposize = {}
        for index, po in enumerate(pkgs):
            if po.repo.deltarpm_percentage == 1:
                continue # Allow people to turn off a repo. (meh)
            if po.state == TS_UPDATE: pass
            elif po.name in ayum.conf.installonlypkgs: pass
            else:
                names = oldrpms.get(po.repo)
                if names is None:
                    # load all locally cached rpms
                    names = oldrpms[po.repo] = {}
                    for rpmfn in os.listdir(po.repo.pkgdir):
                        m = re.match('^(.+)-(.+)-(.+)\.(.+)\.rpm$', rpmfn)
                        if m:
                            n, v, r, a = m.groups()
                            names.setdefault((n, a), set()).add((v, r))
                if (po.name, po.arch) not in names:
                    continue
            pinfo.setdefault(po.repo, {})[po.pkgtup] = index
            reposize[po.repo] = reposize.get(po.repo, 0) + po.size

        # don't use deltas when deltarpm not installed
        if reposize and not os.access(APPLYDELTA, os.X_OK):
            self.verbose_logger.info(_('Delta RPMs disabled because %s not installed.'), APPLYDELTA)
            return

        # download delta metadata
        mdpath = {}
        for repo in reposize:
            for name in ('prestodelta', 'deltainfo'):
                try: data = repo.repoXML.getData(name); break
                except: pass
            else:
                self.verbose_logger.info(_('No Presto metadata available for %s'), repo)
                continue
            path = repo.cachedir +'/'+ os.path.basename(data.location[1])
            if not os.path.exists(path) and int(data.size) > reposize[repo]:
                self.verbose_logger.info(_('Not downloading Presto metadata for %s'), repo)
                continue

            def failfunc(e, name=name, repo=repo):
                mdpath.pop(repo, None)
                if hasattr(e, 'exception'): e = e.exception
                self.verbose_logger.warn(_('Failed to download %s for repository %s: %s'),
                                         name, repo, exception2msg(e))
            kwargs = {}
            if async and repo._async:
                kwargs['failfunc'] = failfunc
                kwargs['async'] = True
            try: mdpath[repo] = repo._retrieveMD(name, **kwargs)
            except Errors.RepoError, e: failfunc(e)
        if async:
            grabber.parallel_wait()

        # parse metadata, create DeltaPackage instances
        for repo, cpath in mdpath.items():
            pinfo_repo = pinfo[repo]
            path = repo_gen_decompress(cpath, 'prestodelta.xml',
                                       cached=repo.cache)
            for ev, el in iterparse(path):
                if el.tag != 'newpackage': continue
                name = el.get('name')
                arch = el.get('arch')
                new = name, arch, el.get('epoch'), el.get('version'), el.get('release')
                index = pinfo_repo.get(new)
                if index is not None:
                    po = pkgs[index]
                    best = po.size * (repo.deltarpm_percentage / 100.0)
                    have = oldrpms.get(repo, {}).get((name, arch), {})
                    for el in el.findall('delta'):
                        size = int(el.find('size').text)
                        if size >= best:
                            continue

                        # can we use this delta?
                        epoch = el.get('oldepoch')
                        ver = el.get('oldversion')
                        rel = el.get('oldrelease')
                        if (ver, rel) in have:
                            oldrpm = '%s/%s-%s-%s.%s.rpm' % (repo.pkgdir, name, ver, rel, arch)
                        else:
                            if not ayum.rpmdb.searchNevra(name, epoch, ver, rel, arch):
                                continue
                            oldrpm = None

                        best = size
                        remote = el.find('filename').text
                        csum = el.find('checksum')
                        csum = csum.get('type'), csum.text
                        pkgs[index] = DeltaPackage(po, size, remote, csum, oldrpm)
                el.clear()

    def wait(self, num=None):
        """ Wait for "num" number of jobs to finish, or all of them. Blocks. """
        if num is None:
            num = len(self.jobs)

        # wait for some jobs, run callbacks
        while num > 0:
            if not self.jobs: # This is probably broken logic, which is bad.
                return
            num -= self._wait(block=True)

    def _wait(self, block=False):
        num = 0

        while self.jobs:
            if block:
                pid, code = os.wait()
            else:
                pid, code = os.waitpid(-1, os.WNOHANG)
                if not pid:
                    break

            # urlgrabber spawns child jobs, too.  But they exit synchronously,
            # so we should never see an unknown pid here.
            assert pid in self.jobs
            po = self.jobs.pop(pid)
            if self.progress:
                self.done += po.rpm.size
                self.progress.update(self.done)
            if code != 0:
                unlink_f(po.rpm.localpath)
                self.adderror(po, _('Delta RPM rebuild failed'))
            elif not po.rpm.verifyLocalPkg():
                self.adderror(po, _('Checksum of the delta-rebuilt RPM failed'))
            else:
                os.unlink(po.localpath)
                po.localpath = po.rpm.localpath # for --downloadonly
            num += 1
        return num

    def rebuild(self, po):
        """ Turn a drpm into an rpm, by adding it to the queue and trying to
            service the queue. """
        self._future_jobs.append(po)
        self.dequeue_max()

    def dequeue_all(self):
        """ De-Queue all delta rebuilds and spawn the rebuild processes. """

        count = total = 0
        for po in self.jobs.values() + self._future_jobs:
            count += 1
            total += po.rpm.size
        if total:
            self.verbose_logger.info(_('Finishing delta rebuilds of %d package(s) (%s)'),
                                     count, progress.format_number(total))
            if hasattr(progress, 'text_meter_total_size'):
                progress.text_meter_total_size(0)
            self.progress = po.repo.callback
            self.progress.start(text='<locally rebuilding deltarpms>', size=total)
            self.done = 0
        while self._future_jobs:
            self.dequeue()

    def dequeue_max(self):
        """ De-Queue all delta rebuilds we can and spawn the rebuild
            processes. """

        if not self._future_jobs:
            # Just trim the zombies...
            self._wait()
            return

        while self._future_jobs:
            if not self.dequeue(block=False):
                break

    def dequeue(self, block=True):
        """ Try to De-Queue a delta rebuild and spawn the rebuild process. """
        # Do this here, just to keep the zombies at bay...
        self._wait()

        if not self._future_jobs:
            return False

        if self.limit <= len(self.jobs):
            if not block:
                return False
            self.wait((self.limit - len(self.jobs)) + 1)

        po = self._future_jobs.pop(0)
        args = ()
        if po.oldrpm: args += '-r', po.oldrpm
        args += po.localpath, po.rpm.localpath

        pid = os.spawnl(os.P_NOWAIT, APPLYDELTA, APPLYDELTA, *args)
        self.jobs[pid] = po
        return True
