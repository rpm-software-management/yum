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
# Copyright 2010 Red Hat
#
# James Antill <james@fedoraproject.org>

import os
import fnmatch
import re

class InstalledGroup(object):
    def __init__(self, gid):
        self.gid       = gid
        self.pkg_names = set()

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


class InstalledGroups(object):
    def __init__(self, db_path):
        self.filename = db_path + "/installed"
        self.groups   = {}
        self.changed  = False

        if not os.access(self.filename, os.R_OK):
            return

        def _read_str(fo):
            return fo.readline()[:-1]

        fo = open(self.filename)
        ver = int(_read_str(fo))
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

    def close(self):
        pass

    def save(self, force=False):
        if not force and not self.changed:
            return False

        db_path = os.path.dirname(self.filename)
        if not os.path.exists(db_path):
            try:
                os.makedirs(db_path)
            except (IOError, OSError), e:
                # some sort of useful thing here? A warning?
                return False

        if not os.access(db_path, os.W_OK):
            return False

        fo = open(self.filename + '.tmp', 'w')

        fo.write("1\n") # version
        fo.write("%u\n" % len(self.groups))
        for grp in sorted(self.groups.values()):
            fo.write("%s\n" % grp.gid)
            fo.write("%u\n" % len(grp.pkg_names))
            for pkgname in sorted(grp.pkg_names):
                fo.write("%s\n" % pkgname)
        fo.close()
        os.rename(self.filename + '.tmp', self.filename)
        self.changed = False

    def add_group(self, groupid, pkg_names):
        self.changed = True

        if groupid not in self.groups:
            self.groups[groupid] = InstalledGroup(groupid)
        grp = self.groups[groupid]

        for pkg_name in pkg_names:
            grp.pkg_names.add(pkg_name)

    def del_group(self, groupid):
        self.changed = True

        if groupid in self.groups:
            del self.groups[groupid]

    def return_groups(self, group_pattern, case_sensitive=False):
        returns = {}

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
