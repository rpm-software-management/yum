#! /usr/bin/python -tt
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Library General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
#
# Copyright 2010, 2012 Red Hat
#
# James Antill <james@fedoraproject.org>

import os
import fnmatch
import re

def _open_no_umask(*args):
    """ Annoying people like to set umask's for root, which screws everything
        up for user readable stuff. """
    oumask = os.umask(022)
    try:
        ret = open(*args)
    finally:
        os.umask(oumask)

    return ret

def _makedirs_no_umask(*args):
    """ Annoying people like to set umask's for root, which screws everything
        up for user readable stuff. """
    oumask = os.umask(022)
    try:
        ret = os.makedirs(*args)
    finally:
        os.umask(oumask)

    return ret

def _read_str(fo):
    for s in fo:
        if s[:-1]:
            return s[:-1]
    return ''

class InstalledGroup(object):
    def __init__(self, gid):
        self.gid       = gid
        self.pkg_names = set()
        self.environment = None

    def __cmp__(self, other):
        if other is None:
            return 1
        return cmp(self.gid, other.gid)

    def _additions(self, pkg_names):
        pkg_names = set(pkg_names)
        return sorted(pkg_names.difference(self.pkg_names))

    def _removals(self, pkg_names):
        pkg_names = set(pkg_names)
        return sorted(pkg_names.difference(self.pkg_names))

    groupid = property(fget=lambda self: self.gid,
                      fset=lambda self, value: setattr(self, "gid", value),
                      fdel=lambda self: setattr(self, "gid", None),
                      doc="Compat. to treat comps groups/igroups the same")



class InstalledEnvironment(object):
    def __init__(self, evgid):
        self.evgid     = evgid
        self.grp_names = set()

    def __cmp__(self, other):
        if other is None:
            return 1
        return cmp(self.evgid, other.evgid)

    def _additions(self, grp_names):
        grp_names = set(grp_names)
        return sorted(grp_names.difference(self.grp_names))

    def _removals(self, grp_names):
        grp_names = set(grp_names)
        return sorted(grp_names.difference(self.grp_names))

    environmentid = property(fget=lambda self: self.evgid,
                      fset=lambda self, value: setattr(self, "evgid", value),
                      fdel=lambda self: setattr(self, "evgid", None),
                      doc="Compat. to treat comps groups/igroups the same")


class InstalledGroups(object):
    def __init__(self, db_path):
        self.groups   = {}
        self.changed  = False
        self.environments = {}

        self._read_pkg_grps(db_path)
        self._read_grp_grps(db_path)

    def _read_pkg_grps(self, db_path):
        self.filename = db_path + "/installed"
        if not os.access(self.filename, os.R_OK):
            return

        fo = open(self.filename)
        try:
            ver = int(_read_str(fo))
        except ValueError:
            return
        if ver != 1:
            return

        groups_num = int(_read_str(fo))
        while groups_num > 0:
            groups_num -= 1

            grp = InstalledGroup(_read_str(fo))
            self.groups[grp.gid] = grp

            num = int(_read_str(fo))
            while num > 0:
                num -= 1
                grp.pkg_names.add(_read_str(fo))

    def _read_grp_grps(self, db_path):
        self.grp_filename = db_path + "/environment"
        if not os.access(self.grp_filename, os.R_OK):
            return

        fo = open(self.grp_filename)
        try:
            ver = int(_read_str(fo))
        except ValueError:
            return
        if ver != 1:
            return

        groups_num = int(_read_str(fo))
        while groups_num > 0:
            groups_num -= 1

            evgrp = InstalledEnvironment(_read_str(fo))
            self.environments[evgrp.evgid] = evgrp

            num = int(_read_str(fo))
            while num > 0:
                num -= 1
                grpname = _read_str(fo)
                memb = _read_str(fo)
                evgrp.grp_names.add(grpname)
                assert memb in ('true', 'false')
                if memb == 'true':
                    assert grpname in self.groups
                    if grpname in self.groups:
                        self.groups[grpname].environment = evgrp.evgid

    def close(self):
        pass

    def save(self, force=False):
        if not force and not self.changed:
            return False

        db_path = os.path.dirname(self.filename)
        if not os.path.exists(db_path):
            try:
                _makedirs_no_umask(db_path)
            except (IOError, OSError), e:
                # some sort of useful thing here? A warning?
                return False

        if not os.access(db_path, os.W_OK):
            return False

        self._write_pkg_grps()
        self._write_grp_grps()

        self.changed = False

    def _write_pkg_grps(self):
        fo = _open_no_umask(self.filename + '.tmp', 'w')

        fo.write("1\n") # version
        fo.write("%u\n" % len(self.groups))
        for grp in sorted(self.groups.values()):
            fo.write("%s\n" % grp.gid)
            fo.write("%u\n" % len(grp.pkg_names))
            for pkgname in sorted(grp.pkg_names):
                fo.write("%s\n" % pkgname)
        fo.close()
        os.rename(self.filename + '.tmp', self.filename)

    def _write_grp_grps(self):
        fo = _open_no_umask(self.grp_filename + '.tmp', 'w')

        fo.write("1\n") # version
        fo.write("%u\n" % len(self.environments))
        for evgrp in sorted(self.environments.values()):
            fo.write("%s\n" % evgrp.evgid)
            fo.write("%u\n" % len(evgrp.grp_names))
            for grpname in sorted(evgrp.grp_names):
                fo.write("%s\n" % grpname)
                if (grpname in self.groups and
                    self.groups[grpname].environment == evgrp.evgid):
                    fo.write("%s\n" % "true")
                else:
                    fo.write("%s\n" % "false")

        fo.close()
        os.rename(self.grp_filename + '.tmp', self.grp_filename)

    def add_group(self, groupid, pkg_names, ievgrp=None):
        self.changed = True

        if groupid not in self.groups:
            self.groups[groupid] = InstalledGroup(groupid)
        grp = self.groups[groupid]

        for pkg_name in pkg_names:
            grp.pkg_names.add(pkg_name)

        if ievgrp is not None:
            grp.environment = ievgrp.evgid
            ievgrp.grp_names.add(groupid)
        return grp

    def del_group(self, groupid):
        self.changed = True

        if groupid in self.groups:
            del self.groups[groupid]

    def return_groups(self, group_pattern, case_sensitive=False):
        returns = {}

        if not group_pattern:
            return []

        for item in group_pattern.split(','):
            item = item.strip()
            if item in self.groups:
                thisgroup = self.groups[item]
                returns[thisgroup.gid] = thisgroup
                continue
            
            if case_sensitive:
                match = re.compile(fnmatch.translate(item)).match
            else:
                match = re.compile(fnmatch.translate(item), flags=re.I).match

            done = False
            for group in self.groups.values():
                if match(group.gid):
                    done = True
                    returns[group.gid] = group
                    break

        return returns.values()

    def add_environment(self, evgroupid, grp_names):
        self.changed = True

        if evgroupid not in self.environments:
            self.environments[evgroupid] = InstalledEnvironment(evgroupid)
        grp = self.environments[evgroupid]

        for grp_name in grp_names:
            grp.grp_names.add(grp_name)
        return grp

    def del_environment(self, evgroupid):
        self.changed = True

        if evgroupid in self.environments:
            del self.environments[evgroupid]

    def return_environments(self, evgroup_pattern, case_sensitive=False):
        returns = {}

        if not evgroup_pattern:
            return []

        for item in evgroup_pattern.split(','):
            item = item.strip()
            if item in self.environments:
                thisgroup = self.environments[item]
                returns[thisgroup.evgid] = thisgroup
                continue

            if case_sensitive:
                match = re.compile(fnmatch.translate(item)).match
            else:
                match = re.compile(fnmatch.translate(item), flags=re.I).match

            done = False
            for group in self.environments.values():
                if match(group.evgid):
                    done = True
                    returns[group.evgid] = group
                    break

        return returns.values()
