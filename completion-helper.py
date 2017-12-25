#!/usr/bin/python -t
# -*- coding: utf-8 -*-
#
# Copyright (C) 2011 Ville Skyttä
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation,
# Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.


import shlex
import sys

import cli
import yumcommands
from yum.Errors import GroupsError, ConfigError, RepoError


class GroupsCompletionCommand(yumcommands.GroupsCommand):
    def doCommand(self, base, basecmd, extcmds):
        cmd, extcmds = self._grp_cmd(basecmd, extcmds)
        # case insensitivity is fine here because groupinstall etc are that too
        installed, available = base.doGroupLists(
            patterns=[get_pattern(extcmds)])
        if extcmds[0] in ("installed", "all"):
            for group in installed:
                print(group.ui_name)
        if extcmds[0] in ("available", "all"):
            for group in available:
                print(group.ui_name)

class ListCompletionCommand(yumcommands.ListCommand):
    def doCommand(self, base, basecmd, extcmds):
        def printPkgs(pkgs):
            for pkg in pkgs:
                if base.allowedMultipleInstalls(pkg):
                    print(pkg.nvra)
                else:
                    print(pkg.na)

        ypl = base.doPackageLists(pkgnarrow=extcmds[0],
                                  patterns=[get_pattern(extcmds)])
        if extcmds[0] in ("installed", "all"):
            printPkgs(ypl.installed)
        if extcmds[0] in ("available", "all"):
            printPkgs(ypl.available)

class RepoListCompletionCommand(yumcommands.RepoListCommand):
    def doCommand(self, base, basecmd, extcmds):
        import fnmatch
        pattern = get_pattern(extcmds)
        for repo in list(base.repos.repos.values()):
            if fnmatch.fnmatch(repo.id, pattern) \
                    and (extcmds[0] == "all" or
                         (extcmds[0] == "enabled" and repo.isEnabled()) or
                         (extcmds[0] == "disabled" and not repo.isEnabled())):
                print(repo.id)


def get_pattern(extcmds):
    if len(extcmds) > 1:
        try: return shlex.split(extcmds[-1])[0] + "*"
        except (ValueError, IndexError): pass
    return "*"

def main(args):
    base = cli.YumBaseCli()
    base.yum_cli_commands.clear()
    base.registerCommand(GroupsCompletionCommand())
    base.registerCommand(ListCompletionCommand())
    base.registerCommand(RepoListCompletionCommand())
    base.getOptionsConfig(args)
    base.parseCommands()
    try:
        for repo in base.repos.listEnabled():
            repo.skip_if_unavailable = True
        base.doCommands()
    except (GroupsError, ConfigError, RepoError) as e:
    # Any reason to not just catch YumBaseError ?
        base.logger.error(e)

if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except KeyboardInterrupt as e:
        sys.exit(1)
