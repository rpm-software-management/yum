
import os.path
import re

from yum.i18n import _, P_

from yum.constants import *

from yum.logginglevels import INFO_1

import rpmUtils.miscutils

import misc

import fnmatch

# newpackages is weird, in that we'll never display that because we filter to
# things relevant to installed pkgs...
_update_info_types_ = ("security", "bugfix", "enhancement",
                       "recommended", "newpackage")

def _rpm_tup_vercmp(tup1, tup2):
    """ Compare two "std." tuples, (n, a, e, v, r). """
    return rpmUtils.miscutils.compareEVR((tup1[2], tup1[3], tup1[4]),
                                         (tup2[2], tup2[3], tup2[4]))


def _ysp_safe_refs(refs):
    """ Sometimes refs == None, if so return the empty list here. 
        So we don't have to check everywhere. """
    if not refs:
        return []
    return refs

def _match_sec_cmd(sec_cmds, pkgname, notice):
    for i in sec_cmds:
        if fnmatch.fnmatch(pkgname, i):
            return i
        if fnmatch.fnmatch(notice['update_id'], i):
            return i

        cvei = i
        if not (i.startswith("CVE-") or i.startswith("*")):
            cvei = 'CVE-' + i
        for ref in _ysp_safe_refs(notice['references']):
            if ref['id'] is None:
                continue
            if fnmatch.fnmatch(ref['id'], i):
                return i
            if fnmatch.fnmatch(ref['id'], cvei):
                return i
    return None

def _has_id(used_map, refs, ref_type, ref_ids):
    ''' Check if the given ID is a match. '''
    for ref in _ysp_safe_refs(refs):
        if ref['type'] != ref_type:
            continue
        if ref['id'] not in ref_ids:
            continue
        used_map[ref_type][ref['id']] = True
        return ref
    return None
    
def _ysp_should_filter_pkg(opts, pkgname, notice, used_map):
    """ Do the package filtering for should_show and should_keep. """
    
    rcmd = _match_sec_cmd(opts.sec_cmds, pkgname, notice)
    if rcmd:
        used_map['cmd'][rcmd] = True
        return True
    elif opts.advisory and notice['update_id'] in opts.advisory:
        used_map['id'][notice['update_id']] = True
        return True
    elif (opts.severity and notice['type'] == 'security' and
          notice['severity'] in opts.severity):
        used_map['sev'][notice['severity']] = True
        return True
    elif opts.cve and _has_id(used_map, notice['references'], "cve", opts.cve):
        return True
    elif opts.bz and _has_id(used_map, notice['references'],"bugzilla",opts.bz):
        return True
    # FIXME: Add opts for enhancement/etc.? -- __update_info_types__
    elif (opts.security and notice['type'] == 'security' and
          (not opts.severity or 'severity' not in notice or
           not notice['severity'])):
        return True
    elif opts.bugfixes and notice['type'] == 'bugfix':
        return True
    elif not (opts.advisory or opts.cve or opts.bz or
              opts.security or opts.bugfixes or opts.sec_cmds or opts.severity):
        return True # This is only possible from should_show_pkg

    return False

def _ysp_has_info_md(rname, md):
    if rname in _update_info_types_:
        if md['type'] == rname:
            return md
    for ref in _ysp_safe_refs(md['references']):
        if ref['type'] != rname:
            continue
        return md

def _no_options(opts):
    return not (opts.security or opts.bugfixes or
                opts.advisory or opts.bz or opts.cve or opts.severity)

def _updateinfofilter2opts(updateinfo_filters):
    opts = misc.GenericHolder()
    opts.sec_cmds = []

    opts.advisory = updateinfo_filters.get('advs', [])
    opts.bz       = updateinfo_filters.get('bzs',  [])
    opts.cve      = updateinfo_filters.get('cves', [])
    opts.severity = updateinfo_filters.get('sevs', [])

    opts.bugfixes = updateinfo_filters.get('bugfix', False)
    opts.security = updateinfo_filters.get('security', False)

    return opts

def _args2filters(args):
    # Basically allow args to turn into security filters, for shell command etc.

    T_map = {'advs' : 'advs',
             'advisory' : 'advs',
             'advisories' : 'advs',

             'bzs' : 'bzs',
             'bz' : 'bzs',

             'cves' : 'cves',
             'cve' : 'cves',

             'security-severity' : 'sevs',
             'security-severities' : 'sevs',
             'severity' : 'sevs',
             'severities' : 'sevs',
             'sevs' : 'sevs',
             'sev' : 'sevs',

             'security' : 'security',
             'sec' : 'security',

             'bugfix' : 'bugfix',
             'bugfixes' : 'bugfix',
             'bugs' : 'bugfix',

             }

    filters = {'security' : False, 'bugfix' : False}

    for arg0 in args:
        arg0 = arg0.replace(" ", ',')
        T = 'advs'
        if '=' in arg0:
            T, arg1 = arg0.split('=', 1)
        elif arg0 not in T_map:
            arg1 = arg0
        else:
            T = arg0
            arg1 = 'true'

        if T not in T_map:
            continue # Error message?

        T = T_map[T]

        if T in ('security', 'bugfix'):
            filters[T] = not filters[T]
        else:
            filters[T] = filters.get(T, []) + arg1.split(',')
        return filters

def _ysp_gen_opts(filters, sec_cmds=None):
    def strip_respin(id_):
        # Example: RHSA-2016:1234-2 -> RHSA-2016:1234
        pattern = r'^(RH[BES]A\-\d+\:\d+)(\-\d+)?$'
        match = re.match(pattern, id_)
        if match:
            return match.group(1)
        return id_

    opts = _updateinfofilter2opts(filters)
    if sec_cmds is not None:
        opts.sec_cmds = sec_cmds

    # If a RH advisory was specified with a respin suffix, strip it out, as we
    # don't include these suffixes in the notice update_id attribute either (we
    # use the version attribute for that).  Note that there's no ambiguity in
    # which notice version we should match then, as updateinfo.xml should only
    # contain one per advisory ID (we give a warning when duplicate IDs are
    # detected in it).  The reason we are handling this is that we sometimes
    # refer to advisories in this form (e.g. on rhn.redhat.com/errata/...) and
    # the user may then use it with yum too, in which case we would yield no
    # matches.
    #
    # However, we used to put these suffixes in update_id in the past, so let's
    # also keep the original (unstripped) form around in opts, just in case we
    # are dealing with such an old updateinfo.xml.
    for attr in ['sec_cmds', 'advisory']:
        oldlist = getattr(opts, attr)
        stripped = map(strip_respin, oldlist)
        newlist = list(set(oldlist) | set(stripped))
        setattr(opts, attr, newlist)

    return opts

def _ysp_gen_used_map(opts):
    used_map = {'bugzilla' : {}, 'cve' : {}, 'id' : {}, 'cmd' : {}, 'sev' : {}}
    if True:
        return used_map
    for i in opts.sec_cmds:
        used_map['cmd'][i] = False
    for i in opts.advisory:
        used_map['id'][i] = False
    for i in opts.bz:
        used_map['bugzilla'][i] = False
    for i in opts.cve:
        used_map['cve'][i] = False
    for i in opts.severity:
        used_map['sev'][i] = False
    return used_map

def _ysp_chk_used_map(used_map, msg):
    for i in used_map['cmd']:
        if not used_map['cmd'][i]:
            msg('No update information found for \"%s\"' % i)
    for i in used_map['id']:
        if not used_map['id'][i]:
            msg('Advisory \"%s\" not found applicable for this system' % i)
    for i in used_map['bugzilla']:
        if not used_map['bugzilla'][i]:
            msg('BZ \"%s\" not found applicable for this system' % i)
    for i in used_map['cve']:
        if not used_map['cve'][i]:
            msg('CVE \"%s\" not found applicable for this system' % i)
    for i in used_map['sev']:
        if not used_map['sev'][i]:
            msg('Severity \"%s\" not found applicable for this system' % i)


def _get_name2pkgtup(base, pkgtups):
    name2tup = {}
    for pkgtup in pkgtups:
        # Get the latest "old" pkgtups
        if (pkgtup[0] in name2tup and
            _rpm_tup_vercmp(name2tup[pkgtup[0]], pkgtup) > 0):
            continue
        name2tup[pkgtup[0]] = pkgtup
    return name2tup
def _get_name2oldpkgtup(base):
    """ Get the pkgtups for all installed pkgs. which have an update. """
    oupdates = map(lambda x: x[1], base.up.getUpdatesTuples())
    return _get_name2pkgtup(base, oupdates)
def _get_name2instpkgtup(base):
    """ Get the pkgtups for all installed pkgs. """
    return _get_name2pkgtup(base, base.rpmdb.simplePkgList())
def _get_name2allpkgtup(base):
    """ Get the pkgtups for all installed pkgs. and munge that to be the
        first possible pkgtup. """
    ofirst = [(pt[0], pt[1], '0','0','0') for pt in base.rpmdb.simplePkgList()]
    return _get_name2pkgtup(base, ofirst)
def _get_name2aallpkgtup(base):
    """ Get the pkgtups for all available pkgs. and munge that to be the
        first possible pkgtup. """
    ofirst = [(pt[0], pt[1],'0','0','0') for pt in base.pkgSack.simplePkgList()]
    return _get_name2pkgtup(base, ofirst)


#  You might think we'd just call delPackage
# and indeed that works for list updates etc.
#
# __but__ that doesn't work for dependancies on real updates
#
#  So to fix deps. we need to do it at the preresolve stage and take the
# "transaction package list" and then remove packages from that.
#
# __but__ that doesn't work for lists ... so we do it two ways
#
def _ysp_should_keep_pkg(opts, pkgtup, md_info, used_map):
    """ Do we want to keep this package to satisfy the security limits. """
    name = pkgtup[0]
    for (pkgtup, notice) in md_info.get_applicable_notices(pkgtup):
        if _ysp_should_filter_pkg(opts, name, notice, used_map):
            return True
    return False

def _repos_downloaded(repos):
    dled = True
    for repo in repos:
        try:
            data = repo.repoXML.getData('updateinfo');
        except:
            continue # No data is fine...

        # Note that this doesn't check that it's decompressed...
        path = repo.cachedir +'/'+ os.path.basename(data.location[1])
        if not os.path.exists(path):
            dled = False
            break

    return dled

def _check_running_kernel(yb, md_info, msg):
    kern_pkgtup = misc.get_running_kernel_pkgtup(yb.ts)
    if kern_pkgtup[0] is None:
        return

    found_sec = False
    for (pkgtup, notice) in md_info.get_applicable_notices(kern_pkgtup):
        if found_sec or notice['type'] != 'security':
            continue
        found_sec = True
        ipkg = yb.rpmdb.searchPkgTuple(pkgtup)
        if not ipkg:
            continue # Not installed
        ipkg = ipkg[0]

        e = ''
        if kern_pkgtup[2] != '0':
            e = '%s:' % kern_pkgtup[2]
        rpkg = '%s-%s%s-%s.%s' % (kern_pkgtup[0], e,
                                  kern_pkgtup[3], kern_pkgtup[4],
                                  kern_pkgtup[1])

        msg(_('Security: %s is an installed security update') % ipkg)
        msg(_('Security: %s is the currently running version') % rpkg)
        break

def remove_txmbrs(base, filters=None):
    '''
    Remove packages from the transaction, using the updateinfo data.
    '''

    def ysp_del_pkg(tspkg):
        """ Deletes a package within a transaction. """
        base.verbose_logger.log(INFO_1,
                                _(" --> %s from %s removed (updateinfo)") %
                                (tspkg.po, tspkg.po.ui_from_repo))
        tsinfo.remove(tspkg.pkgtup)

    if filters is None:
        filters = base.updateinfo_filters
    opts = _ysp_gen_opts(filters)

    if _no_options(opts):
        return 0, 0, 0

    md_info = base.upinfo
    tot = 0
    cnt = 0
    used_map = _ysp_gen_used_map(opts)
    tsinfo = base.tsInfo
    tspkgs = tsinfo.getMembers()
    #  Ok, here we keep any pkgs that pass "ysp" tests, then we keep all
    # related pkgs ... Ie. "installed" version marked for removal.
    keep_pkgs = set()

    count_states = set(TS_INSTALL_STATES + [TS_ERASE])
    count_pkgs = set()
    for tspkg in tspkgs:
        if tspkg.output_state in count_states:
            count_pkgs.add(tspkg.po)

    name2tup = _get_name2oldpkgtup(base)
    for tspkg in tspkgs:
        if tspkg.output_state in count_states:
            tot += 1
        name = tspkg.po.name
        if (name not in name2tup or
            not _ysp_should_keep_pkg(opts, name2tup[name], md_info, used_map)):
            continue
        if tspkg.output_state in count_states:
            cnt += 1
        keep_pkgs.add(tspkg.po)

    scnt = cnt
    mini_depsolve_again = True
    while mini_depsolve_again:
        mini_depsolve_again = False

        for tspkg in tspkgs:
            if tspkg.po in keep_pkgs:
                # Find any related pkgs, and add them:
                for (rpkg, reason) in tspkg.relatedto:
                    if rpkg not in keep_pkgs:
                        if rpkg in count_pkgs:
                            cnt += 1
                        keep_pkgs.add(rpkg)
                        mini_depsolve_again = True
            else:
                # If related to any keep pkgs, add us
                for (rpkg, reason) in tspkg.relatedto:
                    if rpkg in keep_pkgs:
                        if rpkg in count_pkgs:
                            cnt += 1
                        keep_pkgs.add(tspkg.po)
                        mini_depsolve_again = True
                        break

    for tspkg in tspkgs:
        if tspkg.po not in keep_pkgs:
            ysp_del_pkg(tspkg)

    _ysp_chk_used_map(used_map, lambda x: base.verbose_logger.warn("%s", x))
    
    if cnt:
        base.verbose_logger.log(INFO_1, _('%d package(s) needed (+%d related) for security, out of %d available') % (scnt, cnt - scnt, tot))
    else:
        base.verbose_logger.log(INFO_1, _('No packages needed for security; %d packages available') % tot)

    return cnt, scnt, tot

def exclude_updates(base, filters=None):
    '''
    Exclude all packages to do with updates, using the updateinfo data.
    '''
    
    def ysp_del_pkg(pkg, reason="updateinfo"):
        """ Deletes a package from all trees that yum knows about """
        base.verbose_logger.log(INFO_1,
                                _(" --> %s from %s excluded (%s)") %
                                (pkg,pkg.repoid, reason))
        pkg.repo.sack.delPackage(pkg)

    if filters is None:
        filters = base.updateinfo_filters
    opts = _ysp_gen_opts(filters)

    if _no_options(opts):
        return 0, 0

    md_info = base.upinfo

    used_map = _ysp_gen_used_map(opts)

    tot = len(set(base.doPackageLists(pkgnarrow='updates').updates + \
                  base.doPackageLists(pkgnarrow='obsoletes').obsoletes))

    pkgs = base.pkgSack.returnPackages()
    name2tup = _get_name2oldpkgtup(base)
    
    pkgs_to_del = []
    for pkg in pkgs:
        name = pkg.name
        if (name not in name2tup or
            not _ysp_should_keep_pkg(opts, name2tup[name], md_info, used_map)):
            pkgs_to_del.append(pkg.name)
            continue
    if pkgs_to_del:
        for p in base.doPackageLists(pkgnarrow='available', patterns=pkgs_to_del, showdups=True).available:
            ysp_del_pkg(p)

    cnt = len(set(base.doPackageLists(pkgnarrow='updates').updates + \
                  base.doPackageLists(pkgnarrow='obsoletes').obsoletes))

    _ysp_chk_used_map(used_map, lambda x: base.verbose_logger.warn("%s", x))

    if cnt:
        base.verbose_logger.log(INFO_1, _('%d package(s) needed for security, out of %d available') % (cnt, tot))
    else:
        base.verbose_logger.log(INFO_1, _('No packages needed for security; %d packages available' % tot))

    return cnt, tot

def exclude_all(base, filters=None):
    '''
    Exclude all packages, using the updateinfo data.
    '''
    
    def ysp_del_pkg(pkg, reason="updateinfo"):
        """ Deletes a package from all trees that yum knows about """
        base.verbose_logger.log(INFO_1,
                                _(" --> %s from %s excluded (%s)") %
                                (pkg,pkg.repoid, reason))
        pkg.repo.sack.delPackage(pkg)

    if filters is None:
        filters = base.updateinfo_filters
    opts = _ysp_gen_opts(filters)

    if _no_options(opts):
        return 0, 0

    md_info = base.upinfo

    used_map = _ysp_gen_used_map(opts)

    pkgs = base.pkgSack.returnPackages()
    name2tup = _get_name2aallpkgtup(base)
    
    tot = 0
    cnt = 0
    for pkg in pkgs:
        tot += 1
        name = pkg.name
        if (name not in name2tup or
            not _ysp_should_keep_pkg(opts, name2tup[name], md_info, used_map)):
            ysp_del_pkg(pkg)
            continue
        cnt += 1

    _ysp_chk_used_map(used_map, lambda x: base.verbose_logger.warn("%s", x))

    if cnt:
        base.verbose_logger.log(INFO_1, _('%d package(s) needed for security, out of %d available') % (cnt, tot))
    else:
        base.verbose_logger.log(INFO_1, _('No packages needed for security; %d packages available' % tot))

    return cnt, tot

def update_minimal(base, extcmds=[]):
    """Mark the specified items to be updated, in the minimal way.
    :param extcmds: the user specified arguments
    :return: a list of transaction members added to the
       transaction set by this function
    """
    txmbrs = []

    used_map = _ysp_gen_used_map(base.updateinfo_filters)
    opts     = _ysp_gen_opts(base.updateinfo_filters)
    ndata    = _no_options(opts)

    # NOTE: Not doing obsoletes processing atm. ... maybe we should? --
    # Also worth pointing out we don't go backwards for obsoletes in the:
    # update --security case etc.

    # obsoletes = base.up.getObsoletesTuples(newest=False)
    # for (obsoleting, installed) in sorted(obsoletes, key=lambda x: x[0]):
    #   pass

    # Tuples == (n, a, e, v, r)
    oupdates  = map(lambda x: x[1], base.up.getUpdatesTuples())
    for oldpkgtup in sorted(oupdates):
        data = base.upinfo.get_applicable_notices(oldpkgtup)
        if ndata: # No options means pick the oldest update
            data.reverse()

        for (pkgtup, notice) in data:
            name = pkgtup[0]
            if extcmds and not _match_sec_cmd(extcmds, name, notice):
                continue
            if (not ndata and
                not _ysp_should_filter_pkg(opts, name, notice, used_map)):
                continue
            txmbrs.extend(base.update(name=pkgtup[0], arch=pkgtup[1],
                                      epoch=pkgtup[2],
                                      version=pkgtup[3], release=pkgtup[4]))
            break

    # _ysp_chk_used_map(used_map, msg)

    return txmbrs

