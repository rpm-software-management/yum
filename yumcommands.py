#!/usr/bin/python -t
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
# Copyright 2006 Duke University 
# Written by Seth Vidal

"""
Classes for subcommands of the yum command line interface.
"""

import os
import cli
from yum import logginglevels
from yum import _, P_
from yum import misc
import yum.Errors
import operator
import locale
import fnmatch
import time
from yum.i18n import utf8_width, utf8_width_fill, to_unicode, exception2msg
import tempfile
import glob

import yum.config

def _err_mini_usage(base, basecmd):
    if basecmd not in base.yum_cli_commands:
        base.usage()
        return
    cmd = base.yum_cli_commands[basecmd]
    txt = base.yum_cli_commands["help"]._makeOutput(cmd)
    base.logger.critical(_(' Mini usage:\n'))
    base.logger.critical(txt)

def checkRootUID(base):
    """Verify that the program is being run by the root user.

    :param base: a :class:`yum.Yumbase` object.
    :raises: :class:`cli.CliError`
    """
    if base.conf.uid != 0:
        base.logger.critical(_('You need to be root to perform this command.'))
        raise cli.CliError

def checkGPGKey(base):
    """Verify that there are gpg keys for the enabled repositories in the
    rpm database.

    :param base: a :class:`yum.Yumbase` object.
    :raises: :class:`cli.CliError`
    """
    if base._override_sigchecks:
        return
    if not base.gpgKeyCheck():
        for repo in base.repos.listEnabled():
            if (repo.gpgcheck or repo.repo_gpgcheck) and not repo.gpgkey:
                msg = _("""
You have enabled checking of packages via GPG keys. This is a good thing. 
However, you do not have any GPG public keys installed. You need to download
the keys for packages you wish to install and install them.
You can do that by running the command:
    rpm --import public.gpg.key


Alternatively you can specify the url to the key you would like to use
for a repository in the 'gpgkey' option in a repository section and yum 
will install it for you.

For more information contact your distribution or package provider.
""")
                base.logger.critical(msg)
                base.logger.critical(_("Problem repository: %s"), repo)
                raise cli.CliError

def checkPackageArg(base, basecmd, extcmds):
    """Verify that *extcmds* contains the name of at least one package for
    *basecmd* to act on.

    :param base: a :class:`yum.Yumbase` object.
    :param basecmd: the name of the command being checked for
    :param extcmds: a list of arguments passed to *basecmd*
    :raises: :class:`cli.CliError`
    """
    if len(extcmds) == 0:
        base.logger.critical(
                _('Error: Need to pass a list of pkgs to %s') % basecmd)
        _err_mini_usage(base, basecmd)
        raise cli.CliError

def checkSwapPackageArg(base, basecmd, extcmds):
    """Verify that *extcmds* contains the name of at least two packages for
    *basecmd* to act on.

    :param base: a :class:`yum.Yumbase` object.
    :param basecmd: the name of the command being checked for
    :param extcmds: a list of arguments passed to *basecmd*
    :raises: :class:`cli.CliError`
    """
    min_args = 2
    if '--' in extcmds:
        min_args = 3
    if len(extcmds) < min_args:
        base.logger.critical(
                _('Error: Need at least two packages to %s') % basecmd)
        _err_mini_usage(base, basecmd)
        raise cli.CliError

def checkRepoPackageArg(base, basecmd, extcmds):
    """Verify that *extcmds* contains the name of at least one package for
    *basecmd* to act on.

    :param base: a :class:`yum.Yumbase` object.
    :param basecmd: the name of the command being checked for
    :param extcmds: a list of arguments passed to *basecmd*
    :raises: :class:`cli.CliError`
    """
    if len(extcmds) < 2: # <repoid> install|remove [pkgs]
        base.logger.critical(
                _('Error: Need to pass a repoid. and command to %s') % basecmd)
        _err_mini_usage(base, basecmd)
        raise cli.CliError

    repos = base.repos.findRepos(extcmds[0])
    if not repos:
        base.logger.critical(
                _('Error: Need to pass a single valid repoid. to %s') % basecmd)
        _err_mini_usage(base, basecmd)
        raise cli.CliError

    if len(repos) != 1 or repos[0].id != extcmds[0]:
        base.logger.critical(
                _('Error: Need to pass a single valid repoid. to %s') % basecmd)
        _err_mini_usage(base, basecmd)
        raise cli.CliError
    if not repos[0].isEnabled():
        base.logger.critical(
                _('Error: Repo %s is not enabled') % extcmds[0])
        _err_mini_usage(base, basecmd)
        raise cli.CliError


def checkItemArg(base, basecmd, extcmds):
    """Verify that *extcmds* contains the name of at least one item for
    *basecmd* to act on.  Generally, the items are command-line
    arguments that are not the name of a package, such as a file name
    passed to provides.

    :param base: a :class:`yum.Yumbase` object.
    :param basecmd: the name of the command being checked for
    :param extcmds: a list of arguments passed to *basecmd*
    :raises: :class:`cli.CliError`
    """
    if len(extcmds) == 0:
        base.logger.critical(_('Error: Need an item to match'))
        _err_mini_usage(base, basecmd)
        raise cli.CliError

def checkGroupArg(base, basecmd, extcmds):
    """Verify that *extcmds* contains the name of at least one group for
    *basecmd* to act on.

    :param base: a :class:`yum.Yumbase` object.
    :param basecmd: the name of the command being checked for
    :param extcmds: a list of arguments passed to *basecmd*
    :raises: :class:`cli.CliError`
    """
    if len(extcmds) == 0:
        base.logger.critical(_('Error: Need a group or list of groups'))
        _err_mini_usage(base, basecmd)
        raise cli.CliError    

def checkCleanArg(base, basecmd, extcmds):
    """Verify that *extcmds* contains at least one argument, and that all
    arguments in *extcmds* are valid options for clean.

    :param base: a :class:`yum.Yumbase` object
    :param basecmd: the name of the command being checked for
    :param extcmds: a list of arguments passed to *basecmd*
    :raises: :class:`cli.CliError`
    """
    VALID_ARGS = ('headers', 'packages', 'metadata', 'dbcache', 'plugins',
                  'expire-cache', 'rpmdb', 'all')

    if len(extcmds) == 0:
        base.logger.critical(_('Error: clean requires an option: %s') % (
            ", ".join(VALID_ARGS)))
        raise cli.CliError

    for cmd in extcmds:
        if cmd not in VALID_ARGS:
            base.logger.critical(_('Error: invalid clean argument: %r') % cmd)
            _err_mini_usage(base, basecmd)
            raise cli.CliError

def checkShellArg(base, basecmd, extcmds):
    """Verify that the arguments given to 'yum shell' are valid.  yum
    shell can be given either no argument, or exactly one argument,
    which is the name of a file.

    :param base: a :class:`yum.Yumbase` object.
    :param basecmd: the name of the command being checked for
    :param extcmds: a list of arguments passed to *basecmd*
    :raises: :class:`cli.CliError`
    """
    if len(extcmds) == 0:
        base.verbose_logger.debug(_("No argument to shell"))
    elif len(extcmds) == 1:
        base.verbose_logger.debug(_("Filename passed to shell: %s"), 
            extcmds[0])              
        if not os.path.isfile(extcmds[0]):
            base.logger.critical(
                _("File %s given as argument to shell does not exist."), 
                extcmds[0])
            base.usage()
            raise cli.CliError
    else:
        base.logger.critical(
                _("Error: more than one file given as argument to shell."))
        base.usage()
        raise cli.CliError

def checkEnabledRepo(base, possible_local_files=[]):
    """Verify that there is at least one enabled repo.

    :param base: a :class:`yum.Yumbase` object.
    :param basecmd: the name of the command being checked for
    :param extcmds: a list of arguments passed to *basecmd*
    :raises: :class:`cli.CliError`:
    """
    if base.repos.listEnabled():
        return

    for lfile in possible_local_files:
        if lfile.endswith(".rpm") and os.path.exists(lfile):
            return

    msg = _('There are no enabled repos.\n'
            ' Run "yum repolist all" to see the repos you have.\n'
            ' You can enable repos with yum-config-manager --enable <repo>')
    base.logger.critical(msg)
    raise cli.CliError

class YumCommand:
    """An abstract base class that defines the methods needed by the cli
    to execute a specific command.  Subclasses must override at least
    :func:`getUsage` and :func:`getSummary`.
    """

    def __init__(self):
        self.done_command_once = False
        self.hidden = False

    def doneCommand(self, base, msg, *args):
        """ Output *msg* the first time that this method is called, and do
        nothing on subsequent calls.  This is to prevent duplicate
        messages from being printed for the same command.

        :param base: a :class:`yum.Yumbase` object
        :param msg: the message to be output
        :param *args: additional arguments associated with the message
        """
        if not self.done_command_once:
            base.verbose_logger.info(logginglevels.INFO_2, msg, *args)
        self.done_command_once = True

    def getNames(self):
        """Return a list of strings that are the names of the command.
        The command can be called from the command line by using any
        of these names.

        :return: a list containing the names of the command
        """
        return []

    def getUsage(self):
        """Return a usage string for the command, including arguments.

        :return: a usage string for the command
        """
        raise NotImplementedError

    def getSummary(self):
        """Return a one line summary of what the command does.

        :return: a one line summary of what the command does
        """
        raise NotImplementedError
    
    def doCheck(self, base, basecmd, extcmds):
        """Verify that various conditions are met so that the command
        can run.

        :param base: a :class:`yum.Yumbase` object.
        :param basecmd: the name of the command being checked for
        :param extcmds: a list of arguments passed to *basecmd*
        """
        pass

    def doCommand(self, base, basecmd, extcmds):
        """Execute the command

        :param base: a :class:`yum.Yumbase` object.
        :param basecmd: the name of the command being executed
        :param extcmds: a list of arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        return 0, [_('Nothing to do')]
    
    def needTs(self, base, basecmd, extcmds):
        """Return whether a transaction set must be set up before the
        command can run

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: True if a transaction set is needed, False otherwise
        """
        return True

    #  Some of this is subjective, esp. between past/present, but roughly use:
    #
    # write = I'm using package data to alter the rpmdb in anyway.
    # read-only:future  = I'm providing data that is likely to result in a
    #                     future write, so we might as well do it now.
    #                     Eg. yum check-update && yum update -q -y
    # read-only:present = I'm providing data about the present state of
    #                     packages in the repo.
    #                     Eg. yum list yum
    # read-only:past    = I'm providing context data about past writes, or just
    #                     anything that is available is good enough for me
    #                     (speed is much better than quality).
    #                     Eg. yum history info
    #                     Eg. TAB completion
    #
    # ...default is write, which does the same thing we always did (obey
    # metadata_expire and live with it).
    def cacheRequirement(self, base, basecmd, extcmds):
        """Return the cache requirements for the remote repos.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: Type of requirement: read-only:past, read-only:present, read-only:future, write
        """
        return 'write'
        

class InstallCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    install command.
    """

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of
        these names.

        :return: a list containing the names of this command
        """
        return ['install', 'install-n', 'install-na', 'install-nevra']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return _("PACKAGE...")

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("Install a package or packages on your system")
    
    def doCheck(self, base, basecmd, extcmds):
        """Verify that conditions are met so that this command can run.
        These include that the program is being run by the root user,
        that there are enabled repositories with gpg keys, and that
        this command is called with appropriate arguments.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        """
        checkRootUID(base)
        checkGPGKey(base)
        checkPackageArg(base, basecmd, extcmds)
        checkEnabledRepo(base, extcmds)

    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        self.doneCommand(base, _("Setting up Install Process"))
        try:
            return base.installPkgs(extcmds, basecmd=basecmd)
        except yum.Errors.YumBaseError, e:
            return 1, [exception2msg(e)]


class UpdateCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    update command.
    """

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can by called from the command line by using any of
        these names.

        :return: a list containing the names of this command
        """
        return ['update', 'update-to']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return _("[PACKAGE...]")

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("Update a package or packages on your system")

    def doCheck(self, base, basecmd, extcmds):
        """Verify that conditions are met so that this command can run.
        These include that there are enabled repositories with gpg
        keys, and that this command is being run by the root user.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        """
        checkRootUID(base)
        checkGPGKey(base)
        checkEnabledRepo(base, extcmds)

    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        self.doneCommand(base, _("Setting up Update Process"))
        try:
            return base.updatePkgs(extcmds, update_to=(basecmd == 'update-to'))
        except yum.Errors.YumBaseError, e:
            return 1, [exception2msg(e)]

class DistroSyncCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    distro-synch command.
    """

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['distribution-synchronization', 'distro-sync']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return _("[PACKAGE...]")

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("Synchronize installed packages to the latest available versions")

    def doCheck(self, base, basecmd, extcmds):
        """Verify that conditions are met so that this command can run.
        These include that the program is being run by the root user,
        and that there are enabled repositories with gpg keys.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        """
        checkRootUID(base)
        checkGPGKey(base)
        checkEnabledRepo(base, extcmds)

    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        self.doneCommand(base, _("Setting up Distribution Synchronization Process"))
        try:
            base.conf.obsoletes = 1
            return base.distroSyncPkgs(extcmds)
        except yum.Errors.YumBaseError, e:
            return 1, [exception2msg(e)]

def _add_pkg_simple_list_lens(data, pkg, indent=''):
    """ Get the length of each pkg's column. Add that to data.
        This "knows" about simpleList and printVer. """
    na  = len(pkg.name)    + 1 + len(pkg.arch)    + len(indent)
    ver = len(pkg.version) + 1 + len(pkg.release)
    rid = len(pkg.ui_from_repo)
    if pkg.epoch != '0':
        ver += len(pkg.epoch) + 1
    for (d, v) in (('na', na), ('ver', ver), ('rid', rid)):
        data[d].setdefault(v, 0)
        data[d][v] += 1

def _list_cmd_calc_columns(base, ypl):
    """ Work out the dynamic size of the columns to pass to fmtColumns. """
    data = {'na' : {}, 'ver' : {}, 'rid' : {}}
    for lst in (ypl.installed, ypl.available, ypl.extras,
                ypl.updates, ypl.recent):
        for pkg in lst:
            _add_pkg_simple_list_lens(data, pkg)
    if len(ypl.obsoletes) > 0:
        for (npkg, opkg) in ypl.obsoletesTuples:
            _add_pkg_simple_list_lens(data, npkg)
            _add_pkg_simple_list_lens(data, opkg, indent=" " * 4)

    data = [data['na'], data['ver'], data['rid']]
    columns = base.calcColumns(data, remainder_column=1)
    return (-columns[0], -columns[1], -columns[2])

class InfoCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    info command.
    """

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['info']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return "[PACKAGE|all|available|installed|updates|distro-extras|extras|obsoletes|recent]"

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("Display details about a package or group of packages")

    def doCommand(self, base, basecmd, extcmds, repoid=None):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        try:
            highlight = base.term.MODE['bold']
            #  If we are doing: "yum info installed blah" don't do the highlight
            # because the usability of not accessing the repos. is still higher
            # than providing colour for a single line. Usable updatesd/etc. FTW.
            if basecmd == 'info' and extcmds and extcmds[0] == 'installed':
                highlight = False
            ypl = base.returnPkgLists(extcmds, installed_available=highlight,
                                      repoid=repoid)
        except yum.Errors.YumBaseError, e:
            return 1, [exception2msg(e)]
        else:
            update_pkgs = {}
            inst_pkgs   = {}
            local_pkgs  = {}

            columns = None
            if basecmd == 'list':
                # Dynamically size the columns
                columns = _list_cmd_calc_columns(base, ypl)

            if highlight and ypl.installed:
                #  If we have installed and available lists, then do the
                # highlighting for the installed packages so you can see what's
                # available to update, an extra, or newer than what we have.
                for pkg in (ypl.hidden_available +
                            ypl.reinstall_available +
                            ypl.old_available):
                    key = (pkg.name, pkg.arch)
                    if key not in update_pkgs or pkg.verGT(update_pkgs[key]):
                        update_pkgs[key] = pkg

            if highlight and ypl.available:
                #  If we have installed and available lists, then do the
                # highlighting for the available packages so you can see what's
                # available to install vs. update vs. old.
                for pkg in ypl.hidden_installed:
                    key = (pkg.name, pkg.arch)
                    if key not in inst_pkgs or pkg.verGT(inst_pkgs[key]):
                        inst_pkgs[key] = pkg

            if highlight and ypl.updates:
                # Do the local/remote split we get in "yum updates"
                for po in sorted(ypl.updates):
                    if po.repo.id != 'installed' and po.verifyLocalPkg():
                        local_pkgs[(po.name, po.arch)] = po

            # Output the packages:
            kern = base.conf.color_list_installed_running_kernel
            clio = base.conf.color_list_installed_older
            clin = base.conf.color_list_installed_newer
            clir = base.conf.color_list_installed_reinstall
            clie = base.conf.color_list_installed_extra
            rip = base.listPkgs(ypl.installed, _('Installed Packages'), basecmd,
                                highlight_na=update_pkgs, columns=columns,
                                highlight_modes={'>' : clio, '<' : clin,
                                                 'kern' : kern,
                                                 '=' : clir, 'not in' : clie})
            kern = base.conf.color_list_available_running_kernel
            clau = base.conf.color_list_available_upgrade
            clad = base.conf.color_list_available_downgrade
            clar = base.conf.color_list_available_reinstall
            clai = base.conf.color_list_available_install
            rap = base.listPkgs(ypl.available, _('Available Packages'), basecmd,
                                highlight_na=inst_pkgs, columns=columns,
                                highlight_modes={'<' : clau, '>' : clad,
                                                 'kern' : kern,
                                                 '=' : clar, 'not in' : clai})
            rep = base.listPkgs(ypl.extras, _('Extra Packages'), basecmd,
                                columns=columns)
            cul = base.conf.color_update_local
            cur = base.conf.color_update_remote
            rup = base.listPkgs(ypl.updates, _('Updated Packages'), basecmd,
                                highlight_na=local_pkgs, columns=columns,
                                highlight_modes={'=' : cul, 'not in' : cur})

            # XXX put this into the ListCommand at some point
            if len(ypl.obsoletes) > 0 and basecmd == 'list': 
            # if we've looked up obsolete lists and it's a list request
                rop = [0, '']
                print _('Obsoleting Packages')
                # The tuple is (newPkg, oldPkg) ... so sort by new
                for obtup in sorted(ypl.obsoletesTuples,
                                    key=operator.itemgetter(0)):
                    base.updatesObsoletesList(obtup, 'obsoletes',
                                              columns=columns, repoid=repoid)
            else:
                rop = base.listPkgs(ypl.obsoletes, _('Obsoleting Packages'),
                                    basecmd, columns=columns)
            rrap = base.listPkgs(ypl.recent, _('Recently Added Packages'),
                                 basecmd, columns=columns)
            # extcmds is pop(0)'d if they pass a "special" param like "updates"
            # in returnPkgLists(). This allows us to always return "ok" for
            # things like "yum list updates".
            if len(extcmds) and \
               rrap[0] and rop[0] and rup[0] and rep[0] and rap[0] and rip[0]:
                return 1, [_('No matching Packages to list')]
            return 0, []

    def needTs(self, base, basecmd, extcmds):
        """Return whether a transaction set must be set up before this
        command can run.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: True if a transaction set is needed, False otherwise
        """
        if len(extcmds) and extcmds[0] == 'installed':
            return False
        
        return True

    def cacheRequirement(self, base, basecmd, extcmds):
        """Return the cache requirements for the remote repos.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: Type of requirement: read-only:past, read-only:present, read-only:future, write
        """
        if len(extcmds) and extcmds[0] in ('updates', 'obsoletes'):
            return 'read-only:future'
        if len(extcmds) and extcmds[0] in ('installed', 'distro-extras', 'extras', 'recent'):
            return 'read-only:past'
        # available/all
        return 'read-only:present'


class ListCommand(InfoCommand):
    """A class containing methods needed by the cli to execute the
    list command.
    """

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['list']

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("List a package or groups of packages")


class EraseCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    erase command.
    """

        
    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['erase', 'remove', 'autoremove',
                'erase-n', 'erase-na', 'erase-nevra',
                'autoremove-n', 'autoremove-na', 'autoremove-nevra',
                'remove-n', 'remove-na', 'remove-nevra']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return "PACKAGE..."

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("Remove a package or packages from your system")

    def doCheck(self, base, basecmd, extcmds):
        """Verify that conditions are met so that this command can
        run.  These include that the program is being run by the root
        user, and that this command is called with appropriate
        arguments.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        """
        checkRootUID(base)
        if basecmd == 'autoremove':
            return
        checkPackageArg(base, basecmd, extcmds)

    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """

        pos = False
        if basecmd.startswith('autoremove'):
            #  We have to alter this, as it's used in resolving stage. Which
            # sucks. Just be careful in "yum shell".
            base.conf.clean_requirements_on_remove = True

            basecmd = basecmd[len('auto'):] # pretend it's just remove...

            if not extcmds:
                pos = True
                extcmds = []
                for pkg in sorted(base.rpmdb.returnLeafNodes()):
                    if 'reason' not in pkg.yumdb_info:
                        continue
                    if pkg.yumdb_info.reason != 'dep':
                        continue
                    extcmds.append(pkg)

        self.doneCommand(base, _("Setting up Remove Process"))
        try:
            ret = base.erasePkgs(extcmds, pos=pos, basecmd=basecmd)
        except yum.Errors.YumBaseError, e:
            ret = (1, [exception2msg(e)])

        return ret

    def needTs(self, base, basecmd, extcmds):
        """Return whether a transaction set must be set up before this
        command can run.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: True if a transaction set is needed, False otherwise
        """
        return False

    def needTsRemove(self, base, basecmd, extcmds):
        """Return whether a transaction set for removal only must be
        set up before this command can run.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: True if a remove-only transaction set is needed, False otherwise
        """
        return True

 
class GroupsCommand(YumCommand):
    """ Single sub-command interface for most groups interaction. """

    direct_commands = {'grouplist'    : 'list',
                       'groupinstall' : 'install',
                       'groupupdate'  : 'install',
                       'groupremove'  : 'remove',
                       'grouperase'   : 'remove',
                       'groupinfo'    : 'info'}

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['groups', 'group'] + self.direct_commands.keys()

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return "[list|info|summary|install|upgrade|remove|mark] [GROUP]"

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("Display, or use, the groups information")
    
    def _grp_setup_doCommand(self, base):
        self.doneCommand(base, _("Setting up Group Process"))

        base.doRepoSetup(dosack=0)
        try:
            base.doGroupSetup()
        except yum.Errors.GroupsError:
            return 1, [_('No Groups on which to run command')]
        except yum.Errors.YumBaseError, e:
            return 1, [exception2msg(e)]

    def _grp_cmd(self, basecmd, extcmds):
        if basecmd in self.direct_commands:
            cmd = self.direct_commands[basecmd]
        elif extcmds:
            cmd = extcmds[0]
            extcmds = extcmds[1:]
        else:
            cmd = 'summary'

        if cmd in ('mark', 'unmark') and extcmds:
            cmd = "%s-%s" % (cmd, extcmds[0])
            extcmds = extcmds[1:]

        remap = {'update' : 'upgrade',
                 'erase' : 'remove',
                 'mark-erase' : 'mark-remove',
                 }
        cmd = remap.get(cmd, cmd)

        return cmd, extcmds

    def doCheck(self, base, basecmd, extcmds):
        """Verify that conditions are met so that this command can run.
        The exact conditions checked will vary depending on the
        subcommand that is being called.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        """
        cmd, extcmds = self._grp_cmd(basecmd, extcmds)

        checkEnabledRepo(base)
        ocmds_all = []
        ocmds_arg = []
        if base.conf.group_command == 'objects':
            ocmds_arg = ('mark-install', 'mark-remove',
                         'mark-packages', 'mark-packages-force',
                         'unmark-packages',
                         'mark-packages-sync', 'mark-packages-sync-force',
                         'mark-groups', 'mark-groups-force',
                         'unmark-groups',
                         'mark-groups-sync', 'mark-groups-sync-force')

            ocmds_all = ('mark-install', 'mark-remove', 'mark-convert',
                         'mark-packages', 'mark-packages-force',
                         'unmark-packages',
                         'mark-packages-sync', 'mark-packages-sync-force',
                         'mark-groups', 'mark-groups-force',
                         'unmark-groups',
                         'mark-groups-sync', 'mark-groups-sync-force')

        if cmd in ('install', 'remove', 'info') or cmd in ocmds_arg:
            checkGroupArg(base, cmd, extcmds)

        if cmd in ('install', 'remove', 'upgrade') or cmd in ocmds_all:
            checkRootUID(base)

        if cmd in ('install', 'upgrade'):
            checkGPGKey(base)

        cmds = set(('list', 'info', 'remove', 'install', 'upgrade', 'summary'))
        if base.conf.group_command == 'objects':
            cmds.update(ocmds_all)

        if cmd not in cmds:
            base.logger.critical(_('Invalid groups sub-command, use: %s.'),
                                 ", ".join(cmds))
            raise cli.CliError

        if base.conf.group_command != 'objects':
            pass
        elif not os.path.exists(base.igroups.filename):
            base.logger.critical(_("There is no installed groups file."))
        elif not os.access(base.igroups.filename, os.R_OK):
            base.logger.critical(_("You don't have access to the groups DB."))
            raise cli.CliError

    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        cmd, extcmds = self._grp_cmd(basecmd, extcmds)

        self._grp_setup_doCommand(base)
        if cmd == 'summary':
            return base.returnGroupSummary(extcmds)

        if cmd == 'list':
            return base.returnGroupLists(extcmds)

        try:
            if cmd == 'info':
                return base.returnGroupInfo(extcmds)
            if cmd == 'install':
                return base.installGroups(extcmds)
            if cmd == 'upgrade':
                return base.installGroups(extcmds, upgrade=True)
            if cmd == 'remove':
                return base.removeGroups(extcmds)

            if cmd == 'mark-install':
                gRG = base._groupReturnGroups(extcmds,ignore_case=False)
                igrps, grps, ievgrps, evgrps = gRG
                for evgrp in evgrps:
                    base.igroups.add_environment(evgrp.environmentid,
                                                 evgrp.allgroups)
                for grp in grps:
                    base.igroups.add_group(grp.groupid, grp.packages)
                base.igroups.save()
                return 0, ['Marked install: ' + ','.join(extcmds)]

            if cmd in ('mark-packages', 'mark-packages-force'):
                if len(extcmds) < 2:
                    return 1, ['No group or package given']
                gRG = base._groupReturnGroups([extcmds[0]],
                                              ignore_case=False)
                igrps, grps, ievgrps, evgrps = gRG
                if igrps is None or len(igrps) != 1:
                    return 1, ['No group matched']
                grp = igrps[0]
                force = cmd == 'mark-packages-force'
                for pkg in base.rpmdb.returnPackages(patterns=extcmds[1:]):
                    if not force and 'group_member' in pkg.yumdb_info:
                        continue
                    pkg.yumdb_info.group_member = grp.gid
                    grp.pkg_names.add(pkg.name)
                    base.igroups.changed = True
                base.igroups.save()
                return 0, ['Marked packages: ' + ','.join(extcmds[1:])]

            if cmd == 'unmark-packages':
                for pkg in base.rpmdb.returnPackages(patterns=extcmds):
                    if 'group_member' in pkg.yumdb_info:
                        del pkg.yumdb_info.group_member
                return 0, ['UnMarked packages: ' + ','.join(extcmds)]

            if cmd in ('mark-packages-sync', 'mark-packages-sync-force'):
                gRG = base._groupReturnGroups(extcmds,ignore_case=False)
                igrps, grps, ievgrps, evgrps = gRG
                if not igrps:
                    return 1, ['No group matched']
                force = cmd == 'mark-packages-sync-force'
                for grp in igrps:
                    for pkg in base.rpmdb.searchNames(grp.pkg_names):
                        if not force and 'group_member' in pkg.yumdb_info:
                            continue
                        pkg.yumdb_info.group_member = grp.gid
                if force:
                    return 0, ['Marked packages-sync-force: '+','.join(extcmds)]
                else:
                    return 0, ['Marked packages-sync: ' + ','.join(extcmds)]

            if cmd in ('mark-groups', 'mark-groups-force'):
                if len(extcmds) < 2:
                    return 1, ['No environment or group given']
                gRG = base._groupReturnGroups([extcmds[0]],
                                              ignore_case=False)
                igrps, grps, ievgrps, evgrps = gRG
                if ievgrps is None or len(ievgrps) != 1:
                    return 1, ['No environment matched']
                evgrp = ievgrps[0]
                force = cmd == 'mark-groups-force'
                gRG = base._groupReturnGroups(extcmds[1:], ignore_case=False)
                for grp in gRG[1]:
                    # Packages full or empty?
                    self.igroups.add_group(grp.groupid,
                                           grp.packages, ievgrp)
                if force:
                    for grp in gRG[0]:
                        grp.environment = evgrp.evgid
                        base.igroups.changed = True
                base.igroups.save()
                return 0, ['Marked groups: ' + ','.join(extcmds[1:])]

            if cmd == 'unmark-groups':
                gRG = base._groupReturnGroups([extcmds[0]],
                                              ignore_case=False)
                igrps, grps, ievgrps, evgrps = gRG
                if igrps is None:
                    return 1, ['No groups matched']
                for grp in igrps:
                    grp.environment = None
                    base.igroups.changed = True
                base.igroups.save()
                return 0, ['UnMarked groups: ' + ','.join(extcmds)]

            if cmd in ('mark-groups-sync', 'mark-groups-sync-force'):
                gRG = base._groupReturnGroups(extcmds,ignore_case=False)
                igrps, grps, ievgrps, evgrps = gRG
                if not ievgrps:
                    return 1, ['No environment matched']
                force = cmd == 'mark-groups-sync-force'
                for evgrp in ievgrps:
                    grp_names = ",".join(sorted(evgrp.grp_names))
                    for grp in base.igroups.return_groups(grp_names):
                        if not force and grp.environment is not None:
                            continue
                        grp.environment = evgrp.evgid
                        base.igroups.changed = True
                base.igroups.save()
                if force:
                    return 0, ['Marked groups-sync-force: '+','.join(extcmds)]
                else:
                    return 0, ['Marked groups-sync: ' + ','.join(extcmds)]

            # FIXME: This doesn't do environment groups atm.
            if cmd == 'mark-convert':
                # Convert old style info. into groups as objects.

                def _convert_grp(grp):
                    if not grp.installed:
                        return
                    pkg_names = grp.packages
                    base.igroups.add_group(grp.groupid, pkg_names)

                    for pkg in base.rpmdb.searchNames(pkg_names):
                        if 'group_member' in pkg.yumdb_info:
                            continue
                        pkg.yumdb_info.group_member = grp.groupid

                # Blank everything.
                for gid in base.igroups.groups.keys():
                    base.igroups.del_group(gid)
                for pkg in base.rpmdb:
                    if 'group_member' in pkg.yumdb_info:
                        del pkg.yumdb_info.group_member

                #  Need to do this by hand, when using objects, to setup the
                # .installed attribute in comps.
                base.comps.compile(base.rpmdb.simplePkgList())

                #  This is kind of a hack, to work around the biggest problem
                # with having pkgs in more than one group. Treat Fedora/EL/etc.
                # base/core special. Maybe other groups?

                #  Not 100% we want to force install "core", as that's then
                # "different", but it is better ... so, meh.
                special_gids = (('core', True),
                                ('base', False))
                for gid, force_installed in special_gids:
                    grp = base.comps.return_group(gid)
                    if grp is None:
                        continue
                    if force_installed:
                        grp.installed = True
                    _convert_grp(grp)
                for grp in base.comps.get_groups():
                    if grp.groupid in special_gids:
                        continue
                    _convert_grp(grp)
                    
                base.igroups.save()
                return 0, ['Converted old style groups to objects.']

            if cmd == 'mark-remove':
                gRG = base._groupReturnGroups(extcmds,ignore_case=False)
                igrps, grps, ievgrps, evgrps = gRG
                for evgrp in ievgrps:
                    base.igroups.del_environment(evgrp.evgid)
                for grp in igrps:
                    base.igroups.del_group(grp.gid)
                base.igroups.save()
                return 0, ['Marked remove: ' + ','.join(extcmds)]


        except yum.Errors.YumBaseError, e:
            return 1, [exception2msg(e)]


    def needTs(self, base, basecmd, extcmds):
        """Return whether a transaction set must be set up before this
        command can run.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: True if a transaction set is needed, False otherwise
        """
        cmd, extcmds = self._grp_cmd(basecmd, extcmds)

        if cmd in ('list', 'info', 'remove', 'summary'):
            return False
        if cmd.startswith('mark') or cmd.startswith('unmark'):
            return False
        return True

    def needTsRemove(self, base, basecmd, extcmds):
        """Return whether a transaction set for removal only must be
        set up before this command can run.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: True if a remove-only transaction set is needed, False otherwise
        """
        cmd, extcmds = self._grp_cmd(basecmd, extcmds)

        if cmd in ('remove',):
            return True
        return False

class MakeCacheCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    makecache command.
    """

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['makecache']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return ""

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("Generate the metadata cache")

    def doCheck(self, base, basecmd, extcmds):
        """Verify that conditions are met so that this command can
        run; namely that there is an enabled repository.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        """
        checkEnabledRepo(base)

    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        base.logger.debug(_("Making cache files for all metadata files."))
        base.logger.debug(_("This may take a while depending on the speed of this computer"))
        try:
            for repo in base.repos.findRepos('*'):
                repo.metadata_expire = 0
                repo.mdpolicy = "group:all"
            base.doRepoSetup(dosack=0)
            base.repos.doSetup()
            
            # These convert the downloaded data into usable data,
            # we can't remove them until *LoadRepo() can do:
            # 1. Download a .sqlite.bz2 and convert to .sqlite
            # 2. Download a .xml.gz and convert to .xml.gz.sqlite
            base.repos.populateSack(mdtype='all', cacheonly=1)

            # Now decompress stuff, so that -C works, sigh.
            fname_map = {'group_gz'   : 'groups.xml',
                         'pkgtags'    : 'pkgtags.sqlite',
                         'updateinfo' : 'updateinfo.xml',
                         'prestodelta': 'prestodelta.xml',
                         }
            for repo in base.repos.listEnabled():
                for MD in repo.repoXML.fileTypes():
                    if MD not in fname_map:
                        continue
                    misc.repo_gen_decompress(repo.retrieveMD(MD),
                                             fname_map[MD],
                                             cached=repo.cache)

        except yum.Errors.YumBaseError, e:
            return 1, [exception2msg(e)]
        return 0, [_('Metadata Cache Created')]

    def needTs(self, base, basecmd, extcmds):
        """Return whether a transaction set must be set up before this
        command can run.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: True if a transaction set is needed, False otherwise
        """
        return False

class CleanCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    clean command.
    """
    
    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['clean']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return "[headers|packages|metadata|dbcache|plugins|expire-cache|all]"

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("Remove cached data")

    def doCheck(self, base, basecmd, extcmds):
        """Verify that conditions are met so that this command can run.
        These include that there is at least one enabled repository,
        and that this command is called with appropriate arguments.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        """
        checkCleanArg(base, basecmd, extcmds)
        checkEnabledRepo(base)
        
    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        base.conf.cache = 1
        return base.cleanCli(extcmds)

    def needTs(self, base, basecmd, extcmds):
        """Return whether a transaction set must be set up before this
        command can run.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: True if a transaction set is needed, False otherwise
        """
        return False

    def cacheRequirement(self, base, basecmd, extcmds):
        """Return the cache requirements for the remote repos.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: Type of requirement: read-only:past, read-only:present, read-only:future, write
        """
        return 'read-only:past'


class ProvidesCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    provides command.
    """

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['provides', 'whatprovides']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return "SOME_STRING"
    
    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("Find what package provides the given value")

    def doCheck(self, base, basecmd, extcmds):
        """Verify that conditions are met so that this command can
        run; namely that this command is called with appropriate arguments.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        """
        checkItemArg(base, basecmd, extcmds)

    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        base.logger.debug("Searching Packages: ")
        try:
            return base.provides(extcmds)
        except yum.Errors.YumBaseError, e:
            return 1, [exception2msg(e)]

    def cacheRequirement(self, base, basecmd, extcmds):
        """Return the cache requirements for the remote repos.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: Type of requirement: read-only:past, read-only:present, read-only:future, write
        """
        return 'read-only:past'


class CheckUpdateCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    check-update command.
    """

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['check-update']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return "[PACKAGE...]"

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("Check for available package updates")

    def doCheck(self, base, basecmd, extcmds):
        """Verify that conditions are met so that this command can
        run; namely that there is at least one enabled repository.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        """
        checkEnabledRepo(base)

    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        obscmds = ['obsoletes'] + extcmds
        base.extcmds.insert(0, 'updates')
        result = 0
        try:
            ypl = base.returnPkgLists(extcmds)
            if (base.conf.obsoletes or
                base.verbose_logger.isEnabledFor(logginglevels.DEBUG_3)):
                typl = base.returnPkgLists(obscmds)
                ypl.obsoletes = typl.obsoletes
                ypl.obsoletesTuples = typl.obsoletesTuples

            columns = _list_cmd_calc_columns(base, ypl)
            if len(ypl.updates) > 0:
                local_pkgs = {}
                highlight = base.term.MODE['bold']
                if highlight:
                    # Do the local/remote split we get in "yum updates"
                    for po in sorted(ypl.updates):
                        if po.repo.id != 'installed' and po.verifyLocalPkg():
                            local_pkgs[(po.name, po.arch)] = po

                cul = base.conf.color_update_local
                cur = base.conf.color_update_remote
                base.listPkgs(ypl.updates, '', outputType='list',
                              highlight_na=local_pkgs, columns=columns,
                              highlight_modes={'=' : cul, 'not in' : cur})
                result = 100
            if len(ypl.obsoletes) > 0: # This only happens in verbose mode
                print _('Obsoleting Packages')
                # The tuple is (newPkg, oldPkg) ... so sort by new
                for obtup in sorted(ypl.obsoletesTuples,
                                    key=operator.itemgetter(0)):
                    base.updatesObsoletesList(obtup, 'obsoletes',
                                              columns=columns)
                result = 100
        except yum.Errors.YumBaseError, e:
            return 1, [exception2msg(e)]
        else:
            return result, []

    def cacheRequirement(self, base, basecmd, extcmds):
        """Return the cache requirements for the remote repos.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: Type of requirement: read-only:past, read-only:present, read-only:future, write
        """
        return 'read-only:future'


class SearchCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    search command.
    """

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['search']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return "SOME_STRING"

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("Search package details for the given string")

    def doCheck(self, base, basecmd, extcmds):
        """Verify that conditions are met so that this command can
        run; namely that this command is called with appropriate arguments.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        """
        checkItemArg(base, basecmd, extcmds)

    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        base.logger.debug(_("Searching Packages: "))
        try:
            return base.search(extcmds)
        except yum.Errors.YumBaseError, e:
            return 1, [exception2msg(e)]

    def needTs(self, base, basecmd, extcmds):
        """Return whether a transaction set must be set up before this
        command can run.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: True if a transaction set is needed, False otherwise
        """
        return False

    def cacheRequirement(self, base, basecmd, extcmds):
        """Return the cache requirements for the remote repos.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: Type of requirement: read-only:past, read-only:present, read-only:future, write
        """
        return 'read-only:present'


class UpgradeCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    upgrade command.
    """

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['upgrade', 'upgrade-to']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return 'PACKAGE...'

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("Update packages taking obsoletes into account")

    def doCheck(self, base, basecmd, extcmds):
        """Verify that conditions are met so that this command can
         run.  These include that the program is being run by the root
         user, and that there are enabled repositories with gpg.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        """
        checkRootUID(base)
        checkGPGKey(base)
        checkEnabledRepo(base, extcmds)

    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        base.conf.obsoletes = 1
        self.doneCommand(base, _("Setting up Upgrade Process"))
        try:
            return base.updatePkgs(extcmds, update_to=(basecmd == 'upgrade-to'))
        except yum.Errors.YumBaseError, e:
            return 1, [exception2msg(e)]

class LocalInstallCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    localinstall command.
    """

    def __init__(self):
        YumCommand.__init__(self)
        self.hidden = True

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['localinstall', 'localupdate']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return "FILE"

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("Install a local RPM")

    def doCheck(self, base, basecmd, extcmds):
        """Verify that conditions are met so that this command can
        run.  These include that there are enabled repositories with
        gpg keys, and that this command is called with appropriate
        arguments.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        """
        checkRootUID(base)
        checkGPGKey(base)
        checkPackageArg(base, basecmd, extcmds)
        
    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is:

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        self.doneCommand(base, _("Setting up Local Package Process"))

        updateonly = basecmd == 'localupdate'
        try:
            return base.localInstall(filelist=extcmds, updateonly=updateonly)
        except yum.Errors.YumBaseError, e:
            return 1, [exception2msg(e)]

    def needTs(self, base, basecmd, extcmds):
        """Return whether a transaction set must be set up before this
        command can run.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: True if a transaction set is needed, False otherwise
        """
        return False

class ResolveDepCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    resolvedep command.
    """

    def __init__(self):
        YumCommand.__init__(self)
        self.hidden = True

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['resolvedep']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return "DEPENDENCY"

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return "repoquery --pkgnarrow=all --whatprovides --qf '%{envra} %{ui_from_repo}'"

    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        base.logger.debug(_("Searching Packages for Dependency:"))
        try:
            return base.resolveDepCli(extcmds)
        except yum.Errors.YumBaseError, e:
            return 1, [exception2msg(e)]

    def cacheRequirement(self, base, basecmd, extcmds):
        """Return the cache requirements for the remote repos.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: Type of requirement: read-only:past, read-only:present, read-only:future, write
        """
        return 'read-only:past'


class ShellCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    shell command.
    """

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['shell']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return "[FILENAME]"

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("Run an interactive yum shell")

    def doCheck(self, base, basecmd, extcmds):
        """Verify that conditions are met so that this command can
        run; namely that this command is called with appropriate arguments.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        """
        checkShellArg(base, basecmd, extcmds)

    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        self.doneCommand(base, _('Setting up Yum Shell'))
        try:
            return base.doShell()
        except yum.Errors.YumBaseError, e:
            return 1, [exception2msg(e)]

    def needTs(self, base, basecmd, extcmds):
        """Return whether a transaction set must be set up before this
        command can run.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: True if a transaction set is needed, False otherwise
        """
        return False


class DepListCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    deplist command.
    """

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['deplist']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return 'PACKAGE...'

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("List a package's dependencies")

    def doCheck(self, base, basecmd, extcmds):
        """Verify that conditions are met so that this command can
        run; namely that this command is called with appropriate
        arguments.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        """
        checkPackageArg(base, basecmd, extcmds)

    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        self.doneCommand(base, _("Finding dependencies: "))
        try:
            return base.deplist(extcmds)
        except yum.Errors.YumBaseError, e:
            return 1, [exception2msg(e)]

    def cacheRequirement(self, base, basecmd, extcmds):
        """Return the cache requirements for the remote repos.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: Type of requirement: read-only:past, read-only:present, read-only:future, write
        """
        return 'read-only:past' # read-only ?


class RepoListCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    repolist command.
    """
    
    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ('repolist', 'repoinfo')

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return '[all|enabled|disabled]'

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _('Display the configured software repositories')

    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        def _repo_size(repo):
            ret = 0
            for pkg in repo.sack.returnPackages():
                ret += pkg.packagesize
            return base.format_number(ret)

        def _repo_match(repo, patterns):
            rid = repo.id.lower()
            rnm = repo.name.lower()
            for pat in patterns:
                if fnmatch.fnmatch(rid, pat):
                    return True
                if fnmatch.fnmatch(rnm, pat):
                    return True
            return False

        def _num2ui_num(num):
            return to_unicode(locale.format("%d", num, True))

        if len(extcmds) >= 1 and extcmds[0] in ('all', 'disabled', 'enabled'):
            arg = extcmds[0]
            extcmds = extcmds[1:]
        else:
            arg = 'enabled'
        extcmds = map(lambda x: x.lower(), extcmds)

        if basecmd == 'repoinfo':
            verbose = True
        else:
            verbose = base.verbose_logger.isEnabledFor(logginglevels.DEBUG_3)
        if arg != 'disabled' or extcmds:
            try:
                # Setup so len(repo.sack) is correct
                base.repos.populateSack()
                base.pkgSack # Need to setup the pkgSack, so excludes work
            except yum.Errors.RepoError:
                if verbose:
                    raise
                #  populate them by hand, so one failure doesn't kill everything
                # after it.
                for repo in base.repos.listEnabled():
                    try:
                        base.repos.populateSack(repo.id)
                    except yum.Errors.RepoError:
                        pass

        repos = base.repos.repos.values()
        repos.sort()
        enabled_repos = base.repos.listEnabled()
        on_ehibeg = base.term.FG_COLOR['green'] + base.term.MODE['bold']
        on_dhibeg = base.term.FG_COLOR['red']
        on_hiend  = base.term.MODE['normal']
        tot_num = 0
        cols = []
        for repo in repos:
            if len(extcmds) and not _repo_match(repo, extcmds):
                continue
            (ehibeg, dhibeg, hiend)  = '', '', ''
            ui_enabled      = ''
            ui_endis_wid    = 0
            ui_num          = ""
            ui_excludes_num = ''
            force_show = False
            if arg == 'all' or repo.id in extcmds or repo.name in extcmds:
                force_show = True
                (ehibeg, dhibeg, hiend) = (on_ehibeg, on_dhibeg, on_hiend)
            if repo in enabled_repos:
                enabled = True
                if arg == 'enabled':
                    force_show = False
                elif arg == 'disabled' and not force_show:
                    continue
                if force_show or verbose:
                    ui_enabled = ehibeg + _('enabled') + hiend
                    ui_endis_wid = utf8_width(_('enabled'))
                    if not verbose:
                        ui_enabled += ": "
                        ui_endis_wid += 2
                if verbose:
                    ui_size = _repo_size(repo)
                # We don't show status for list disabled
                if arg != 'disabled' or verbose:
                    if verbose or base.conf.exclude or repo.exclude:
                        num        = len(repo.sack.simplePkgList())
                    else:
                        num        = len(repo.sack)
                    ui_num     = _num2ui_num(num)
                    excludes   = repo.sack._excludes
                    excludes   = len([pid for r,pid in excludes if r == repo])
                    if excludes:
                        ui_excludes_num = _num2ui_num(excludes)
                        if not verbose:
                            ui_num += "+%s" % ui_excludes_num
                    tot_num   += num
            else:
                enabled = False
                if arg == 'disabled':
                    force_show = False
                elif arg == 'enabled' and not force_show:
                    continue
                ui_enabled = dhibeg + _('disabled') + hiend
                ui_endis_wid = utf8_width(_('disabled'))

            if not verbose:
                rid = repo.ui_id # can't use str()
                if enabled and repo.metalink:
                    mdts = repo.metalink_data.repomd.timestamp
                    if mdts > repo.repoXML.timestamp:
                        rid = '*' + rid
                cols.append((rid, repo.name,
                             (ui_enabled, ui_endis_wid), ui_num))
            else:
                if enabled:
                    md = repo.repoXML
                else:
                    md = None
                out = [base.fmtKeyValFill(_("Repo-id      : "), repo.ui_id),
                       base.fmtKeyValFill(_("Repo-name    : "), repo.name)]

                if force_show or extcmds:
                    out += [base.fmtKeyValFill(_("Repo-status  : "),
                                               ui_enabled)]
                if md and md.revision is not None:
                    out += [base.fmtKeyValFill(_("Repo-revision: "),
                                               md.revision)]
                if md and md.tags['content']:
                    tags = md.tags['content']
                    out += [base.fmtKeyValFill(_("Repo-tags    : "),
                                               ", ".join(sorted(tags)))]

                if md and md.tags['distro']:
                    for distro in sorted(md.tags['distro']):
                        tags = md.tags['distro'][distro]
                        out += [base.fmtKeyValFill(_("Repo-distro-tags: "),
                                                   "[%s]: %s" % (distro,
                                                   ", ".join(sorted(tags))))]

                if md:
                    out += [base.fmtKeyValFill(_("Repo-updated : "),
                                               time.ctime(md.timestamp)),
                            base.fmtKeyValFill(_("Repo-pkgs    : "),ui_num),
                            base.fmtKeyValFill(_("Repo-size    : "),ui_size)]

                if hasattr(repo, '_orig_baseurl'):
                    baseurls = repo._orig_baseurl
                else:
                    baseurls = repo.baseurl
                if baseurls:
                    out += [base.fmtKeyValFill(_("Repo-baseurl : "),
                                               ", ".join(baseurls))]

                if enabled:
                    # This needs to be here due to the mirrorlists are
                    # metalinks hack.
                    repo.urls
                if repo.metalink:
                    out += [base.fmtKeyValFill(_("Repo-metalink: "),
                                               repo.metalink)]
                    if enabled:
                        ts = repo.metalink_data.repomd.timestamp
                        out += [base.fmtKeyValFill(_("  Updated    : "),
                                                   time.ctime(ts))]
                elif repo.mirrorlist:
                    out += [base.fmtKeyValFill(_("Repo-mirrors : "),
                                               repo.mirrorlist)]
                if enabled and repo.urls and not baseurls:
                    url = repo.urls[0]
                    if len(repo.urls) > 1:
                        url += ' (%d more)' % (len(repo.urls) - 1)
                    out += [base.fmtKeyValFill(_("Repo-baseurl : "), url)]

                if not os.path.exists(repo.metadata_cookie):
                    last = _("Unknown")
                else:
                    last = os.stat(repo.metadata_cookie).st_mtime
                    last = time.ctime(last)

                if repo.metadata_expire <= -1:
                    num = _("Never (last: %s)") % last
                elif not repo.metadata_expire:
                    num = _("Instant (last: %s)") % last
                else:
                    num = _num2ui_num(repo.metadata_expire)
                    num = _("%s second(s) (last: %s)") % (num, last)

                out += [base.fmtKeyValFill(_("Repo-expire  : "), num)]

                if repo.exclude:
                    out += [base.fmtKeyValFill(_("Repo-exclude : "),
                                               ", ".join(repo.exclude))]

                if repo.includepkgs:
                    out += [base.fmtKeyValFill(_("Repo-include : "),
                                               ", ".join(repo.includepkgs))]

                if ui_excludes_num:
                    out += [base.fmtKeyValFill(_("Repo-excluded: "),
                                               ui_excludes_num)]

                if repo.repofile:
                    out += [base.fmtKeyValFill(_("Repo-filename: "),
                                               repo.repofile)]

                base.verbose_logger.info("%s\n",
                                        "\n".join(map(misc.to_unicode, out)))

        if not verbose and cols:
            #  Work out the first (id) and last (enabled/disalbed/count),
            # then chop the middle (name)...
            id_len = utf8_width(_('repo id'))
            nm_len = 0
            st_len = 0
            ui_len = 0

            for (rid, rname, (ui_enabled, ui_endis_wid), ui_num) in cols:
                if id_len < utf8_width(rid):
                    id_len = utf8_width(rid)
                if nm_len < utf8_width(rname):
                    nm_len = utf8_width(rname)
                if st_len < (ui_endis_wid + len(ui_num)):
                    st_len = (ui_endis_wid + len(ui_num))
                # Need this as well as above for: utf8_width_fill()
                if ui_len < len(ui_num):
                    ui_len = len(ui_num)
            if arg == 'disabled': # Don't output a status column.
                left = base.term.columns - (id_len + 1)
            elif utf8_width(_('status')) > st_len:
                left = base.term.columns - (id_len + utf8_width(_('status')) +2)
            else:
                left = base.term.columns - (id_len + st_len + 2)

            if left < nm_len: # Name gets chopped
                nm_len = left
            else: # Share the extra...
                left -= nm_len
                id_len += left / 2
                nm_len += left - (left / 2)

            txt_rid  = utf8_width_fill(_('repo id'), id_len)
            txt_rnam = utf8_width_fill(_('repo name'), nm_len, nm_len)
            if arg == 'disabled': # Don't output a status column.
                base.verbose_logger.info("%s %s",
                                        txt_rid, txt_rnam)
            else:
                base.verbose_logger.info("%s %s %s",
                                        txt_rid, txt_rnam, _('status'))
            for (rid, rname, (ui_enabled, ui_endis_wid), ui_num) in cols:
                if arg == 'disabled': # Don't output a status column.
                    base.verbose_logger.info("%s %s",
                                            utf8_width_fill(rid, id_len),
                                            utf8_width_fill(rname, nm_len,
                                                            nm_len))
                    continue

                if ui_num:
                    ui_num = utf8_width_fill(ui_num, ui_len, left=False)
                base.verbose_logger.info("%s %s %s%s",
                                        utf8_width_fill(rid, id_len),
                                        utf8_width_fill(rname, nm_len, nm_len),
                                        ui_enabled, ui_num)

        return 0, ['repolist: ' +to_unicode(locale.format("%d", tot_num, True))]

    def needTs(self, base, basecmd, extcmds):
        """Return whether a transaction set must be set up before this
        command can run.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: True if a transaction set is needed, False otherwise
        """
        return False

    def cacheRequirement(self, base, basecmd, extcmds):
        """Return the cache requirements for the remote repos.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: Type of requirement: read-only:past, read-only:present, read-only:future, write
        """
        return 'read-only:past'


class HelpCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    help command.
    """


    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['help']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return "COMMAND"

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("Display a helpful usage message")

    def doCheck(self, base, basecmd, extcmds):
        """Verify that conditions are met so that this command can
        run; namely that this command is called with appropriate
        arguments.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        """
        if len(extcmds) == 0:
            base.usage()
            raise cli.CliError
        elif len(extcmds) > 1 or extcmds[0] not in base.yum_cli_commands:
            base.usage()
            raise cli.CliError

    @staticmethod
    def _makeOutput(command):
        canonical_name = command.getNames()[0]

        # Check for the methods in case we have plugins that don't
        # implement these.
        # XXX Remove this once usage/summary are common enough
        try:
            usage = command.getUsage()
        except (AttributeError, NotImplementedError):
            usage = None
        try:
            summary = command.getSummary()
        except (AttributeError, NotImplementedError):
            summary = None

        # XXX need detailed help here, too
        help_output = ""
        if usage is not None:
            help_output += "%s %s" % (canonical_name, usage)
        if summary is not None:
            help_output += "\n\n%s" % summary

        if usage is None and summary is None:
            help_output = _("No help available for %s") % canonical_name

        command_names = command.getNames()
        if len(command_names) > 1:
            if len(command_names) > 2:
                help_output += _("\n\naliases: ")
            else:
                help_output += _("\n\nalias: ")
            help_output += ', '.join(command.getNames()[1:])

        return help_output

    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        if extcmds[0] in base.yum_cli_commands:
            command = base.yum_cli_commands[extcmds[0]]
            base.verbose_logger.info(self._makeOutput(command))
        return 0, []

    def needTs(self, base, basecmd, extcmds):
        """Return whether a transaction set must be set up before this
        command can run.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: True if a transaction set is needed, False otherwise
        """
        return False

class ReInstallCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    reinstall command.
    """

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['reinstall']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return "PACKAGE..."

    def doCheck(self, base, basecmd, extcmds):
        """Verify that conditions are met so that this command can
        run.  These include that the program is being run by the root
        user, that there are enabled repositories with gpg keys, and
        that this command is called with appropriate arguments.


        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        """
        checkRootUID(base)
        checkGPGKey(base)
        checkPackageArg(base, basecmd, extcmds)
        checkEnabledRepo(base, extcmds)

    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        self.doneCommand(base, _("Setting up Reinstall Process"))
        try:
            return base.reinstallPkgs(extcmds)
            
        except yum.Errors.YumBaseError, e:
            return 1, [to_unicode(e)]

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("reinstall a package")

    def needTs(self, base, basecmd, extcmds):
        """Return whether a transaction set must be set up before this
        command can run.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: True if a transaction set is needed, False otherwise
        """
        return False
        
class DowngradeCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    downgrade command.
    """

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['downgrade']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return "PACKAGE..."

    def doCheck(self, base, basecmd, extcmds):
        """Verify that conditions are met so that this command can
        run.  These include that the program is being run by the root
        user, that there are enabled repositories with gpg keys, and
        that this command is called with appropriate arguments.


        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        """
        checkRootUID(base)
        checkGPGKey(base)
        checkPackageArg(base, basecmd, extcmds)
        checkEnabledRepo(base, extcmds)

    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        self.doneCommand(base, _("Setting up Downgrade Process"))
        try:
            return base.downgradePkgs(extcmds)
        except yum.Errors.YumBaseError, e:
            return 1, [exception2msg(e)]

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("downgrade a package")

    def needTs(self, base, basecmd, extcmds):
        """Return whether a transaction set must be set up before this
        command can run.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: True if a transaction set is needed, False otherwise
        """
        return False


class VersionCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    version command.
    """

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['version']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return "[all|installed|available]"

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("Display a version for the machine and/or available repos.")

    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        vcmd = 'installed'
        if extcmds:
            vcmd = extcmds[0]

        def _append_repos(cols, repo_data):
            for repoid in sorted(repo_data):
                cur = repo_data[repoid]
                ncols = []
                last_rev = None
                for rev in sorted(cur):
                    if rev is None:
                        continue
                    last_rev = cur[rev]
                    ncols.append(("    %s/%s" % (repoid, rev), str(cur[rev])))
                if None in cur and (not last_rev or cur[None] != last_rev):
                    cols.append(("    %s" % repoid, str(cur[None])))
                cols.extend(ncols)

        verbose = base.verbose_logger.isEnabledFor(logginglevels.DEBUG_3)
        groups = {}
        if vcmd in ('nogroups', 'nogroups-installed', 'nogroups-available',
                    'nogroups-all'):
            gconf = []
            if vcmd == 'nogroups':
                vcmd = 'installed'
            else:
                vcmd = vcmd[len('nogroups-'):]
        else:
            gconf = yum.config.readVersionGroupsConfig()

        for group in gconf:
            groups[group] = set(gconf[group].pkglist)
            if gconf[group].run_with_packages:
                groups[group].update(base.run_with_package_names)

        if vcmd == 'grouplist':
            print _(" Yum version groups:")
            for group in sorted(groups):
                print "   ", group

            return 0, ['version grouplist']

        if vcmd == 'groupinfo':
            for group in groups:
                if group not in extcmds[1:]:
                    continue
                print _(" Group   :"), group
                print _(" Packages:")
                if not verbose:
                    for pkgname in sorted(groups[group]):
                        print "   ", pkgname
                else:
                    data = {'envra' : {}, 'rid' : {}}
                    pkg_names = groups[group]
                    pkg_names2pkgs = base._group_names2aipkgs(pkg_names)
                    base._calcDataPkgColumns(data, pkg_names, pkg_names2pkgs)
                    data = [data['envra'], data['rid']]
                    columns = base.calcColumns(data)
                    columns = (-columns[0], -columns[1])
                    base._displayPkgsFromNames(pkg_names, True, pkg_names2pkgs,
                                               columns=columns)

            return 0, ['version groupinfo']

        rel = base.conf.yumvar['releasever']
        ba  = base.conf.yumvar['basearch']
        cols = []
        if vcmd in ('installed', 'all', 'group-installed', 'group-all'):
            try:
                data = base.rpmdb.simpleVersion(not verbose, groups=groups)
                lastdbv = base.history.last()
                if lastdbv is not None:
                    lastdbv = lastdbv.end_rpmdbversion
                if lastdbv is not None and data[0] != lastdbv:
                    base._rpmdb_warn_checks(warn=lastdbv is not None)
                if vcmd not in ('group-installed', 'group-all'):
                    cols.append(("%s %s/%s" % (_("Installed:"), rel, ba),
                                 str(data[0])))
                    _append_repos(cols, data[1])
                if groups:
                    for grp in sorted(data[2]):
                        if (vcmd.startswith("group-") and
                            len(extcmds) > 1 and grp not in extcmds[1:]):
                            continue
                        cols.append(("%s %s" % (_("Group-Installed:"), grp),
                                     str(data[2][grp])))
                        _append_repos(cols, data[3][grp])
            except yum.Errors.YumBaseError, e:
                return 1, [exception2msg(e)]
        if vcmd in ('available', 'all', 'group-available', 'group-all'):
            try:
                data = base.pkgSack.simpleVersion(not verbose, groups=groups)
                if vcmd not in ('group-available', 'group-all'):
                    cols.append(("%s %s/%s" % (_("Available:"), rel, ba),
                                 str(data[0])))
                    if verbose:
                        _append_repos(cols, data[1])
                if groups:
                    for grp in sorted(data[2]):
                        if (vcmd.startswith("group-") and
                            len(extcmds) > 1 and grp not in extcmds[1:]):
                            continue
                        cols.append(("%s %s" % (_("Group-Available:"), grp),
                                     str(data[2][grp])))
                        if verbose:
                            _append_repos(cols, data[3][grp])
            except yum.Errors.YumBaseError, e:
                return 1, [exception2msg(e)]

        data = {'rid' : {}, 'ver' : {}}
        for (rid, ver) in cols:
            for (d, v) in (('rid', len(rid)), ('ver', len(ver))):
                data[d].setdefault(v, 0)
                data[d][v] += 1
        data = [data['rid'], data['ver']]
        columns = base.calcColumns(data)
        columns = (-columns[0], columns[1])

        for line in cols:
            print base.fmtColumns(zip(line, columns))

        return 0, ['version']

    def needTs(self, base, basecmd, extcmds):
        """Return whether a transaction set must be set up before this
        command can run.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: True if a transaction set is needed, False otherwise
        """
        vcmd = 'installed'
        if extcmds:
            vcmd = extcmds[0]
        verbose = base.verbose_logger.isEnabledFor(logginglevels.DEBUG_3)
        if vcmd == 'groupinfo' and verbose:
            return True
        return vcmd in ('available', 'all', 'group-available', 'group-all')

    def cacheRequirement(self, base, basecmd, extcmds):
        """Return the cache requirements for the remote repos.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: Type of requirement: read-only:past, read-only:present, read-only:future, write
        """
        return 'read-only:present'


class HistoryCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    history command.
    """

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['history']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return "[info|list|packages-list|summary|addon-info|redo|undo|rollback|new]"

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("Display, or use, the transaction history")

    def _hcmd_redo(self, base, extcmds):
        kwargs = {'force_reinstall' : False,
                  'force_changed_removal' : False,
                  }
        kwargs_map = {'reinstall' : 'force_reinstall',
                      'force-reinstall' : 'force_reinstall',
                      'remove' : 'force_changed_removal',
                      'force-remove' : 'force_changed_removal',
                      }
        while len(extcmds) > 1:
            done = False
            for arg in extcmds[1].replace(' ', ',').split(','):
                if arg not in kwargs_map:
                    continue

                done = True
                key = kwargs_map[extcmds[1]]
                kwargs[key] = not kwargs[key]

            if not done:
                break
            extcmds = [extcmds[0]] + extcmds[2:]

        old = base._history_get_transaction(extcmds)
        if old is None:
            return 1, ['Failed history redo']
        tm = time.ctime(old.beg_timestamp)
        print "Repeating transaction %u, from %s" % (old.tid, tm)
        base.historyInfoCmdPkgsAltered(old)
        if base.history_redo(old, **kwargs):
            return 2, ["Repeating transaction %u" % (old.tid,)]

    def _hcmd_undo(self, base, extcmds):
        old = base._history_get_transaction(extcmds)
        if old is None:
            return 1, ['Failed history undo']
        tm = time.ctime(old.beg_timestamp)
        print "Undoing transaction %u, from %s" % (old.tid, tm)
        base.historyInfoCmdPkgsAltered(old)
        if base.history_undo(old):
            return 2, ["Undoing transaction %u" % (old.tid,)]

    def _hcmd_rollback(self, base, extcmds):
        force = False
        if len(extcmds) > 1 and extcmds[1] == 'force':
            force = True
            extcmds = extcmds[:]
            extcmds.pop(0)

        old = base._history_get_transaction(extcmds)
        if old is None:
            return 1, ['Failed history rollback, no transaction']
        last = base.history.last()
        if last is None:
            return 1, ['Failed history rollback, no last?']
        if old.tid == last.tid:
            return 0, ['Rollback to current, nothing to do']

        mobj = None
        for tid in base.history.old(range(old.tid + 1, last.tid + 1)):
            if not force and (tid.altered_lt_rpmdb or tid.altered_gt_rpmdb):
                if tid.altered_lt_rpmdb:
                    msg = "Transaction history is incomplete, before %u."
                else:
                    msg = "Transaction history is incomplete, after %u."
                print msg % tid.tid
                print " You can use 'history rollback force', to try anyway."
                return 1, ['Failed history rollback, incomplete']

            if mobj is None:
                mobj = yum.history.YumMergedHistoryTransaction(tid)
            else:
                mobj.merge(tid)

        tm = time.ctime(old.beg_timestamp)
        print "Rollback to transaction %u, from %s" % (old.tid, tm)
        print base.fmtKeyValFill("  Undoing the following transactions: ",
                                 ", ".join((str(x) for x in mobj.tid)))
        base.historyInfoCmdPkgsAltered(mobj)
        if base.history_undo(mobj):
            return 2, ["Rollback to transaction %u" % (old.tid,)]

    def _hcmd_new(self, base, extcmds):
        base.history._create_db_file()

    def _hcmd_stats(self, base, extcmds):
        print "File        :", base.history._db_file
        num = os.stat(base.history._db_file).st_size
        print "Size        :", locale.format("%d", num, True)
        trans_N = base.history.last()
        if trans_N is None:
            print _("Transactions:"), 0
            return
        counts = base.history._pkg_stats()
        trans_1 = base.history.old("1")[0]
        print _("Transactions:"), trans_N.tid
        print _("Begin time  :"), time.ctime(trans_1.beg_timestamp)
        print _("End time    :"), time.ctime(trans_N.end_timestamp)
        print _("Counts      :")
        print _("  NEVRAC :"), locale.format("%6d", counts['nevrac'], True)
        print _("  NEVRA  :"), locale.format("%6d", counts['nevra'],  True)
        print _("  NA     :"), locale.format("%6d", counts['na'],     True)
        print _("  NEVR   :"), locale.format("%6d", counts['nevr'],   True)
        print _("  rpm DB :"), locale.format("%6d", counts['rpmdb'],  True)
        print _("  yum DB :"), locale.format("%6d", counts['yumdb'],  True)

    def _hcmd_sync(self, base, extcmds):
        extcmds = extcmds[1:]
        if not extcmds:
            extcmds = None
        for ipkg in sorted(base.rpmdb.returnPackages(patterns=extcmds)):
            if base.history.pkg2pid(ipkg, create=False) is None:
                continue

            print "Syncing rpm/yum DB data for:", ipkg, "...",
            if base.history.sync_alldb(ipkg):
                print "Done."
            else:
                print "FAILED."

    def doCheck(self, base, basecmd, extcmds):
        """Verify that conditions are met so that this command can
        run.  The exact conditions checked will vary depending on the
        subcommand that is being called.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        """
        cmds = ('list', 'info', 'summary', 'repeat', 'redo', 'undo', 'new',
                'rollback',
                'addon', 'addon-info',
                'stats', 'statistics', 'sync', 'synchronize'
                'pkg', 'pkgs', 'pkg-list', 'pkgs-list',
                'package', 'package-list', 'packages', 'packages-list',
                'pkg-info', 'pkgs-info', 'package-info', 'packages-info')
        if extcmds and extcmds[0] not in cmds:
            base.logger.critical(_('Invalid history sub-command, use: %s.'),
                                 ", ".join(cmds))
            raise cli.CliError
        if extcmds and extcmds[0] in ('repeat', 'redo', 'undo', 'rollback', 'new'):
            checkRootUID(base)
            checkGPGKey(base)
        elif not os.access(base.history._db_file, os.R_OK):
            base.logger.critical(_("You don't have access to the history DB."))
            raise cli.CliError

    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        vcmd = 'list'
        if extcmds:
            vcmd = extcmds[0]

        if False: pass
        elif vcmd == 'list':
            ret = base.historyListCmd(extcmds)
        elif vcmd == 'info':
            ret = base.historyInfoCmd(extcmds)
        elif vcmd == 'summary':
            ret = base.historySummaryCmd(extcmds)
        elif vcmd in ('addon', 'addon-info'):
            ret = base.historyAddonInfoCmd(extcmds)
        elif vcmd in ('pkg', 'pkgs', 'pkg-list', 'pkgs-list',
                      'package', 'package-list', 'packages', 'packages-list'):
            ret = base.historyPackageListCmd(extcmds)
        elif vcmd == 'undo':
            ret = self._hcmd_undo(base, extcmds)
        elif vcmd in ('redo', 'repeat'):
            ret = self._hcmd_redo(base, extcmds)
        elif vcmd == 'rollback':
            ret = self._hcmd_rollback(base, extcmds)
        elif vcmd == 'new':
            ret = self._hcmd_new(base, extcmds)
        elif vcmd in ('stats', 'statistics'):
            ret = self._hcmd_stats(base, extcmds)
        elif vcmd in ('sync', 'synchronize'):
            ret = self._hcmd_sync(base, extcmds)
        elif vcmd in ('pkg-info', 'pkgs-info', 'package-info', 'packages-info'):
            ret = base.historyPackageInfoCmd(extcmds)

        if ret is None:
            return 0, ['history %s' % (vcmd,)]
        return ret

    def needTs(self, base, basecmd, extcmds):
        """Return whether a transaction set must be set up before this
        command can run.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: True if a transaction set is needed, False otherwise
        """
        vcmd = 'list'
        if extcmds:
            vcmd = extcmds[0]
        return vcmd in ('repeat', 'redo', 'undo', 'rollback')

    def cacheRequirement(self, base, basecmd, extcmds):
        """Return the cache requirements for the remote repos.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: Type of requirement: read-only:past, read-only:present, read-only:future, write
        """
        vcmd = 'list'
        if extcmds:
            vcmd = extcmds[0]
        if vcmd in ('repeat', 'redo', 'undo', 'rollback'):
            return 'write'
        return 'read-only:past'


class CheckRpmdbCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    check-rpmdb command.
    """

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['check', 'check-rpmdb']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return "[dependencies|duplicates|all]"

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("Check for problems in the rpmdb")

    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        chkcmd = 'all'
        if extcmds:
            chkcmd = extcmds

        def _out(x):
            print to_unicode(x.__str__())

        rc = 0
        if base._rpmdb_warn_checks(out=_out, warn=False, chkcmd=chkcmd,
                                   header=lambda x: None):
            rc = 1
        return rc, ['%s %s' % (basecmd, chkcmd)]

    def needTs(self, base, basecmd, extcmds):
        """Return whether a transaction set must be set up before this
        command can run.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: True if a transaction set is needed, False otherwise
        """
        return False

    def cacheRequirement(self, base, basecmd, extcmds):
        """Return the cache requirements for the remote repos.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: Type of requirement: read-only:past, read-only:present, read-only:future, write
        """
        return 'read-only:past'


class LoadTransactionCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    load-transaction command.
    """

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['load-transaction', 'load-ts', 'ts-load']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return "filename"

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("load a saved transaction from filename")

    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """
        def _pkg_avail(l):
            if not l.startswith('mbr:'):
                return True # Kind of ... 

            try:
                pkgtup, current_state = l.split(':')[1].strip().split(' ')
                current_state = int(current_state.strip())
                pkgtup = tuple(pkgtup.strip().split(','))
                if current_state == yum.TS_INSTALL:
                    po = base.getInstalledPackageObject(pkgtup)
                elif current_state == yum.TS_AVAILABLE:
                    po = base.getPackageObject(pkgtup)
                else:
                    return False # Bad...
            except:
                return False # Bad...

            return True

        if not extcmds:
            extcmds = [tempfile.gettempdir()]
        
        load_file = extcmds[0]

        if os.path.isdir(load_file):
            self.doneCommand(base, _("showing transaction files from %s") %
                             load_file)
            yumtxs = sorted(glob.glob("%s/*.yumtx" % load_file))
            currpmv = None
            done = False
            for yumtx in yumtxs:
                data = base._load_ts_data(yumtx)
                if data[0] is not None:
                    continue # Bad file...
                data = data[1]

                rpmv = data[0].strip()
                if currpmv is None:
                    currpmv = str(base.rpmdb.simpleVersion(main_only=True)[0])
                if rpmv == currpmv:
                    current = _('y')
                else:
                    current = ' ' # Not usable is the most common

                # See load_ts() for data ...
                try:
                    numrepos = int(data[2].strip())
                    pkgstart = 3+numrepos
                    numpkgs  = int(data[pkgstart].strip())
                    pkgstart += 1
                except:
                    continue

                # Check to see if all the packages are available..
                bad = ' '
                for l in data[pkgstart:]:
                    l = l.rstrip()
                    if _pkg_avail(l):
                        continue

                    bad = '*'
                    break

                current = '%s%s' % (bad, current)
                if not done:
                    pkgtitle = _("Members")
                    pkglen = utf8_width(pkgtitle)
                    if pkglen < 6:
                        pkglen = 6
                    pkgtitle = utf8_width_fill(pkgtitle, pkglen)
                    print "?? |", pkgtitle, "|", _("Filename")
                    
                    done = True

                numpkgs = "%*s" % (pkglen, locale.format("%d", numpkgs, True))
                print current, '|', numpkgs, '|', os.path.basename(yumtx)
            return 0, [_('Saved transactions from %s; looked at %u files') %
                       (load_file, len(yumtxs))]

        self.doneCommand(base, _("loading transaction from %s") % load_file)
        
        try:
            base.load_ts(load_file)
        except yum.Errors.YumBaseError, e:
            return 1, [to_unicode(e)]
        return 2, [_('Transaction loaded from %s with %s members') % (load_file, len(base.tsInfo.getMembers()))]


    def needTs(self, base, basecmd, extcmds):
        """Return whether a transaction set must be set up before this
        command can run.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: True if a transaction set is needed, False otherwise
        """
        if not extcmds or os.path.isdir(extcmds[0]):
            return False

        return True

    def cacheRequirement(self, base, basecmd, extcmds):
        """Return the cache requirements for the remote repos.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: Type of requirement: read-only:past, read-only:present, read-only:future, write
        """
        if not extcmds or os.path.isdir(extcmds[0]):
            return 'read-only:past'

        return 'write'


class SwapCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    swap command.
    """

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['swap']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return "[remove|cmd] <pkg|arg(s)> [-- install|cmd] <pkg|arg(s)>"

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("Simple way to swap packages, instead of using shell")

    def doCheck(self, base, basecmd, extcmds):
        """Verify that conditions are met so that this command can run.
        These include that the program is being run by the root user,
        that there are enabled repositories with gpg keys, and that
        this command is called with appropriate arguments.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        """
        checkRootUID(base)
        checkGPGKey(base)
        checkSwapPackageArg(base, basecmd, extcmds)
        checkEnabledRepo(base, extcmds)

    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """

        if '--' in extcmds:
            off = extcmds.index('--')
            rextcmds = extcmds[:off]
            iextcmds = extcmds[off+1:]
        else:
            rextcmds = extcmds[:1]
            iextcmds = extcmds[1:]

        if not (rextcmds and iextcmds):
            return 1, ['swap'] # impossible

        if rextcmds[0] not in base.yum_cli_commands:
            rextcmds = ['remove'] + rextcmds
        if iextcmds[0] not in base.yum_cli_commands:
            iextcmds = ['install'] + iextcmds

        # Very similar to what the shell command does...
        ocmds = base.cmds
        oline = base.cmdstring
        for cmds in (rextcmds, iextcmds):
            base.cmdstring = " ".join(cmds)
            base.cmds = cmds
            #  Don't call this atm. as the line has gone through it already,
            # also makes it hard to do the "is ?extcmds[0] a cmd" check.
            # base.plugins.run('args', args=base.cmds)

            # We don't catch exceptions, just pass them up and fail...
            base.parseCommands()
            cmdret = base.doCommands()
            if cmdret[0] != 2:
                return cmdret[0], ['%s %s' % (basecmd, " ".join(cmds))]
        base.cmds      = ocmds
        base.cmdstring = oline

        return 2, ['%s %s' % (basecmd, " ".join(extcmds))]


class RepoPkgsCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    repo command.
    """

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['repo-pkgs',
                'repo-packages', 'repository-pkgs', 'repository-packages']

    def getUsage(self):
        """Return a usage string for this command.

        :return: a usage string for this command
        """
        return "<repoid> <list|info|install|remove|upgrade|remove-or-*> [pkg(s)]"

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("Treat a repo. as a group of packages, so we can install/remove all of them")

    def doCheck(self, base, basecmd, extcmds):
        """Verify that conditions are met so that this command can run.
        These include that the program is being run by the root user,
        that there are enabled repositories with gpg keys, and that
        this command is called with appropriate arguments.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        """
        checkRootUID(base)
        checkGPGKey(base)
        checkRepoPackageArg(base, basecmd, extcmds)
        checkEnabledRepo(base, extcmds)

    def doCommand(self, base, basecmd, extcmds):
        """Execute this command.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        :return: (exit_code, [ errors ])

        exit_code is::

            0 = we're done, exit
            1 = we've errored, exit with error string
            2 = we've got work yet to do, onto the next stage
        """

        def _add_repopkg2txmbrs(txmbrs, repoid):
            for txmbr in txmbrs:
                txmbr.repopkg = repoid

        repoid = extcmds[0]
        cmd = extcmds[1]
        args = extcmds[2:]
        if not args:
            args = ['*']
        num = 0

        remap = {'erase' : 'remove',
                 'erase-or-reinstall' : 'remove-or-reinstall',
                 'erase-or-sync' : 'remove-or-sync',
                 'erase-or-distro-sync' : 'remove-or-sync',
                 'remove-or-distro-sync' : 'remove-or-sync',
                 'erase-or-distribution-synchronization' : 'remove-or-sync',
                 'remove-or-distribution-synchronization' : 'remove-or-sync',
                 'upgrade' : 'update', # Hack, but meh.
                 }
        cmd = remap.get(cmd, cmd)

        if False: pass
        elif cmd == 'list': # list/info is easiest...
            return ListCommand().doCommand(base, cmd, args, repoid=repoid)
        elif cmd == 'info':
            return InfoCommand().doCommand(base, cmd, args, repoid=repoid)

        elif cmd == 'install': # install is simpler version of installPkgs...
            for arg in args:
                txmbrs = base.install(pattern=arg, repoid=repoid)
                _add_repopkg2txmbrs(txmbrs, repoid)
                num += len(txmbrs)

            if num:
                return 2, P_('%d package to install', '%d packages to install',
                             num)

        elif cmd == 'update': # update is basically the same as install...
            for arg in args:
                txmbrs = base.update(pattern=arg, repoid=repoid)
                _add_repopkg2txmbrs(txmbrs, repoid)
                num += len(txmbrs)

            if num:
                return 2, P_('%d package to update', '%d packages to update',
                             num)

        elif cmd == 'remove': # Also mostly the same...
            for arg in args:
                txmbrs = base.remove(pattern=arg, repoid=repoid)
                _add_repopkg2txmbrs(txmbrs, repoid)
                num += len(txmbrs)

            if num:
                return 2, P_('%d package to remove', '%d packages to remove',
                             num)

        elif cmd == 'remove-or-reinstall': # More complicated...
            for arg in args:
                txmbrs = base.remove(pattern=arg, repoid=repoid)
                # Add an install() if it's in another repo.
                for txmbr in txmbrs[:]:
                    pkgs = base.pkgSack.searchPkgTuple(txmbr.pkgtup)
                    for pkg in sorted(pkgs):
                        if pkg.repoid == repoid:
                            continue
                        txmbrs += base.install(po=pkg)
                        break

                _add_repopkg2txmbrs(txmbrs, repoid)
                num += len(txmbrs)

            if num:
                return 2, P_('%d package to remove/reinstall',
                             '%d packages to remove/reinstall', num)

        elif cmd == 'remove-or-sync': # Even more complicated...
            for arg in args:
                txmbrs = base.remove(pattern=arg, repoid=repoid)
                #  Add an install/upgrade/downgrade if a version is in another
                # repo.
                for txmbr in txmbrs[:]:
                    pkgs = base.pkgSack.searchNames([txmbr.name])
                    apkgs = None
                    for pkg in sorted(pkgs):
                        if pkg.repoid == repoid: # Backwards filter_pkgs_repoid
                            continue
                        if apkgs and pkg.verEQ(apkgs[0]):
                            apkgs.append(pkg)
                        else:
                            apkgs = [pkg]

                    if apkgs:
                        for pkg in apkgs:
                            if pkg.arch != txmbr.arch:
                                continue
                            apkgs = [pkg]
                            break
                        if len(apkgs) != 1:
                            apkgs = base.bestPackagesFromList(apkgs)

                    for toinst in apkgs:
                        n,a,e,v,r = toinst.pkgtup
                        if toinst.verEQ(txmbr.po):
                            txmbrs += base.install(po=toinst)
                        elif toinst.verGT(txmbr.po):
                            txmbrs += base.update(po=toinst)
                        else:
                            base.tsInfo.remove(txmbr.pkgtup)
                            txmbrs.remove(txmbr)
                            txmbrs += base.downgrade(po=toinst)

                _add_repopkg2txmbrs(txmbrs, repoid)
                num += len(txmbrs)

            if num:
                return 2, P_('%d package to remove/sync',
                             '%d packages to remove/sync', num)

        else:
            return 1, [_('Not a valid sub-command of %s') % basecmd]

        return 0, [_('Nothing to do')]

    def needTs(self, base, basecmd, extcmds):
        """Return whether a transaction set must be set up before this
        command can run.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: True if a transaction set is needed, False otherwise
        """
        cmd = 'install'
        if len(extcmds) > 1:
            cmd = extcmds[1]
        if cmd in ('info', 'list'):
            return InfoCommand().cacheRequirement(base, cmd, extcmds[2:])

        return True

    def cacheRequirement(self, base, basecmd, extcmds):
        """Return the cache requirements for the remote repos.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: Type of requirement: read-only:past, read-only:present, read-only:future, write
        """
        cmd = 'install'
        if len(extcmds) > 1:
            cmd = extcmds[1]
        if cmd in ('info', 'list'):
            return InfoCommand().cacheRequirement(base, cmd, extcmds[2:])
        return 'write'
