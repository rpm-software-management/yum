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
# Copyright 2013 Red Hat
# Written by Seth Vidal

"""
Classes for subcommands of the yum command line interface.
"""

import os
import sys
import cli
import rpm
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
import shutil
import distutils.spawn
import glob
import errno

import yum.config
from yum import updateinfo

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
    repos = base.repos.findRepos(extcmds[0], name_match=True, ignore_case=True)
    if not repos:
        base.logger.critical(
                _('Error: Need to pass a single valid repoid. to %s') % basecmd)
        _err_mini_usage(base, basecmd)
        raise cli.CliError

    if len(repos) > 1:
        repos = [r for r in repos if r.isEnabled()]

    if len(repos) > 1:
        repos = ", ".join([r.ui_id for r in repos])
        base.logger.critical(
                _('Error: Need to pass only a single valid repoid. to %s, passed: %s') % (basecmd, repos))
        _err_mini_usage(base, basecmd)
        raise cli.CliError
    if not repos[0].isEnabled():
        # Might as well just fix this...
        base.repos.enableRepo(repos[0].id)
        base.verbose_logger.info(
                _('Repo %s has been automatically enabled.') % repos[0].ui_id)
    return repos[0].id


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

    # runs prereposetup (which "most" plugins currently use to add repos.)
    base.pkgSack
    if base.repos.listEnabled():
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
        return base.installPkgs(extcmds, basecmd=basecmd)


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
        ret = base.updatePkgs(extcmds, update_to=(basecmd == 'update-to'))
        updateinfo.remove_txmbrs(base)
        return ret

class DistroSyncCommand(YumCommand):
    """A class containing methods needed by the cli to execute the
    distro-synch command.
    """

    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return ['distribution-synchronization', 'distro-sync', 'distupgrade']

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
        base.conf.obsoletes = 1
        ret = base.distroSyncPkgs(extcmds)
        updateinfo.remove_txmbrs(base)
        return ret

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

        if extcmds and extcmds[0] in ('updates', 'obsoletes'):
            updateinfo.exclude_updates(base)
        else:
            updateinfo.exclude_all(base)

        if True: # Try, YumBase...
            highlight = base.term.MODE['bold']
            #  If we are doing: "yum info installed blah" don't do the highlight
            # because the usability of not accessing the repos. is still higher
            # than providing colour for a single line. Usable updatesd/etc. FTW.
            if basecmd == 'info' and extcmds and extcmds[0] == 'installed':
                highlight = False
            ypl = base.returnPkgLists(extcmds, installed_available=highlight,
                                      repoid=repoid)

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
        return ['erase', 'remove',
                'erase-n', 'erase-na', 'erase-nevra',
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
        ret = base.erasePkgs(extcmds, pos=pos, basecmd=basecmd)

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


class AutoremoveCommand(EraseCommand):
    """A class containing methods needed by the cli to execute the
    autremove command.
    """
    def getNames(self):
        """Return a list containing the names of this command.  This
        command can be called from the command line by using any of these names.

        :return: a list containing the names of this command
        """
        return [ 'autoremove', 'autoremove-n', 'autoremove-na', 'autoremove-nevra']

    def getSummary(self):
        """Return a one line summary of this command.

        :return: a one line summary of this command
        """
        return _("Remove leaf packages")

 
class GroupsCommand(YumCommand):
    """ Single sub-command interface for most groups interaction. """

    direct_commands = {'grouplist'    : 'list',
                       'groupinstall' : 'install',
                       'groupupdate'  : 'update',
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
            raise

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
                         'mark-blacklist',
                         'mark-packages', 'mark-packages-force',
                         'unmark-packages',
                         'mark-packages-sync', 'mark-packages-sync-force',
                         'mark-groups', 'mark-groups-force',
                         'unmark-groups',
                         'mark-groups-sync', 'mark-groups-sync-force')

            ocmds_all = ('mark-install', 'mark-remove', 'mark-convert',
                         'mark-convert-whitelist', 'mark-convert-blacklist',
                         'mark-blacklist',
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
        elif not os.path.exists(os.path.dirname(base.igroups.filename)):
            base.logger.critical(_("There is no installed groups file."))
            base.logger.critical(_("Maybe run: yum groups mark convert (see man yum)"))
        elif not os.access(os.path.dirname(base.igroups.filename), os.R_OK):
            base.logger.critical(_("You don't have access to the groups DBs."))
            raise cli.CliError
        elif not os.path.exists(base.igroups.filename):
            base.logger.critical(_("There is no installed groups file."))
            base.logger.critical(_("Maybe run: yum groups mark convert (see man yum)"))
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

        if True: # Try, YumBase...
            if cmd == 'info':
                return base.returnGroupInfo(extcmds)
            if cmd == 'install':
                return base.installGroups(extcmds)
            if cmd == 'upgrade':
                ret = base.installGroups(extcmds, upgrade=True)
                updateinfo.remove_txmbrs(base)
                return ret
            if cmd == 'remove':
                return base.removeGroups(extcmds)

            if cmd == 'mark-install':
                gRG = base._groupReturnGroups(extcmds,ignore_case=False)
                igrps, grps, ievgrps, evgrps = gRG
                for evgrp in evgrps:
                    base.igroups.add_environment(evgrp.environmentid,
                                                 evgrp.allgroups)
                for grp in grps:
                    pkg_names = set() # Only see names that are installed.
                    for pkg in base.rpmdb.searchNames(grp.packages):
                        pkg_names.add(pkg.name)
                    base.igroups.add_group(grp.groupid, pkg_names)
                base.igroups.save()
                return 0, ['Marked install: ' + ','.join(extcmds)]

            if cmd == 'mark-blacklist':
                gRG = base._groupReturnGroups(extcmds,ignore_case=False)
                igrps, grps, ievgrps, evgrps = gRG
                for ievgrp in ievgrps:
                    evgrp = base.comps.return_environment(igrp.evgid)
                    if not evgrp:
                        continue
                    base.igroups.changed = True
                    ievgrp.grp_names.update(grp.groups)
                for igrp in igrps:
                    grp = base.comps.return_group(igrp.gid)
                    if not grp:
                        continue
                    base.igroups.changed = True
                    igrp.pkg_names.update(grp.packages)
                base.igroups.save()
                return 0, ['Marked upgrade blacklist: ' + ','.join(extcmds)]

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
            if cmd in ('mark-convert',
                       'mark-convert-whitelist', 'mark-convert-blacklist'):
                # Convert old style info. into groups as objects.

                def _convert_grp(grp):
                    if not grp.installed:
                        return
                    pkg_names = []
                    for pkg in base.rpmdb.searchNames(grp.packages):
                        if 'group_member' in pkg.yumdb_info:
                            continue
                        pkg.yumdb_info.group_member = grp.groupid
                        pkg_names.append(pkg.name)

                    #  We only mark the packages installed as a known part of
                    # the group. This way "group update" will work and install
                    # any remaining packages, as it would before the conversion.
                    if cmd == 'mark-convert-whitelist':
                        base.igroups.add_group(grp.groupid, pkg_names)
                    else:
                        base.igroups.add_group(grp.groupid, grp.packages)

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

    def cacheRequirement(self, base, basecmd, extcmds):
        """Return the cache requirements for the remote repos.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: a list of arguments passed to *basecmd*
        :return: Type of requirement: read-only:past, read-only:present, read-only:future, write
        """
        cmd, extcmds = self._grp_cmd(basecmd, extcmds)

        if cmd in ('list', 'info', 'summary'):
            return 'read-only:past'
        if cmd.startswith('mark') or cmd.startswith('unmark'):
            return 'read-only:past'
        return 'write'


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

        # Fast == don't download any extra MD
        fast = False
        if extcmds and extcmds[0] == 'fast':
            fast = True

        if True: # Try, YumBase...
            for repo in base.repos.sort():
                repo.metadata_expire = 0
                if not fast:
                    repo.mdpolicy = "group:all"
            base.doRepoSetup(dosack=0)
            base.repos.doSetup()
            
            # These convert the downloaded data into usable data,
            # we can't remove them until *LoadRepo() can do:
            # 1. Download a .sqlite.bz2 and convert to .sqlite
            # 2. Download a .xml.gz and convert to .xml.gz.sqlite
            if fast:
                #  Can't easily tell which other metadata each repo. has, so
                # just do primary.
                base.repos.populateSack(mdtype='metadata', cacheonly=1)
            else:
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
                    if MD not in repo.retrieved or not repo.retrieved[MD]:
                        continue # For fast mode.
                    misc.repo_gen_decompress(repo.retrieveMD(MD),
                                             fname_map[MD],
                                             cached=repo.cache)

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
        updateinfo.exclude_updates(base)
        return base.provides(extcmds)

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
        return ['check-update',  'check-updates',
                'check-upgrade', 'check-upgrades']

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
        updateinfo.exclude_updates(base)
        obscmds = ['obsoletes'] + extcmds
        extcmds.insert(0, 'updates')
        result = 0
        if True:
            ypl = base.returnPkgLists(extcmds, repoid=repoid)
            if (base.conf.obsoletes or
                base.verbose_logger.isEnabledFor(logginglevels.DEBUG_3)):
                typl = base.returnPkgLists(obscmds, repoid=repoid)
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
                                              columns=columns, repoid=repoid)
                result = 100

            # Add check_running_kernel call, if updateinfo is available.
            if (base.conf.autocheck_running_kernel and
                updateinfo._repos_downloaded(base.repos.listEnabled())):
                def _msg(x):
                    base.verbose_logger.info("%s", x)
                updateinfo._check_running_kernel(base, base.upinfo, _msg)
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
        updateinfo.exclude_updates(base)
        return base.search(extcmds)

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
        ret = base.updatePkgs(extcmds, update_to=(basecmd == 'upgrade-to'))
        updateinfo.remove_txmbrs(base)
        return ret

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
        return base.localInstall(filelist=extcmds, updateonly=updateonly)

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
        updateinfo.exclude_updates(base)
        return base.resolveDepCli(extcmds)

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
        return base.doShell()

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
        updateinfo.exclude_updates(base)
        return base.deplist(extcmds)

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
            for pat in patterns:
                if repo in base.repos.findRepos(pat, name_match=True,
                                                ignore_case=True):
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
                if repo.metadata_expire >= 0:
                    if os.path.exists(repo.metadata_cookie):
                        last = os.stat(repo.metadata_cookie).st_mtime
                        if last + repo.metadata_expire < time.time():
                            rid = '!' + rid
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
        return base.reinstallPkgs(extcmds)

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
        return base.downgradePkgs(extcmds)

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
        if vcmd in ('grouplist', 'groupinfo',
                    'nogroups', 'nogroups-installed', 'nogroups-available',
                    'nogroups-all',
                    'installed', 'all', 'group-installed', 'group-all',
                    'available', 'all', 'group-available', 'group-all'):
            extcmds = extcmds[1:]
        else:
            vcmd = 'installed'

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
                if group not in extcmds:
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

        # Have a way to manually specify a dynamic group of packages, whee.
        if not vcmd.startswith("group-") and extcmds:
            for dgrp in extcmds:
                if '/' not in dgrp:
                    # It's a package name, add it to the cmd line group...
                    if '<cmd line>' not in groups:
                        groups['<cmd line>'] = set()
                    groups['<cmd line>'].add(dgrp)
                else: # It's a file containing a list of packages...
                    if not os.path.exists(dgrp):
                        base.logger.warn(_(" File doesn't exist: %s"), dgrp)
                    else:
                        pkg_names = open(dgrp).readlines()
                        pkg_names = set(n.strip() for n in pkg_names)
                        dgrp = os.path.basename(dgrp)
                        if dgrp in groups:
                            for num in range(1, 100):
                                ndgrp = dgrp + str(num)
                                if ndgrp in groups:
                                    continue
                                dgrp = ndgrp
                                break
                        groups[dgrp] = pkg_names

        rel = base.conf.yumvar['releasever']
        ba  = base.conf.yumvar['basearch']
        cols = []
        if vcmd in ('installed', 'all', 'group-installed', 'group-all'):
            if True: # Try, YumBase...
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
                            extcmds and grp not in extcmds):
                            continue
                        cols.append(("%s %s" % (_("Group-Installed:"), grp),
                                     str(data[2][grp])))
                        _append_repos(cols, data[3][grp])

        if vcmd in ('available', 'all', 'group-available', 'group-all'):
            if True: # Try, YumBase...
                data = base.pkgSack.simpleVersion(not verbose, groups=groups)
                if vcmd not in ('group-available', 'group-all'):
                    cols.append(("%s %s/%s" % (_("Available:"), rel, ba),
                                 str(data[0])))
                    if verbose:
                        _append_repos(cols, data[1])
                if groups:
                    for grp in sorted(data[2]):
                        if (vcmd.startswith("group-") and
                            extcmds and grp not in extcmds):
                            continue
                        cols.append(("%s %s" % (_("Group-Available:"), grp),
                                     str(data[2][grp])))
                        if verbose:
                            _append_repos(cols, data[3][grp])

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
        if not counts:
            msg = _("could not open history file: %s") % base.history._db_file
            raise yum.Errors.MiscError, msg
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
        elif not (base.history._db_file and os.access(base.history._db_file, os.R_OK)):
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
        def _pkg_counts(l, counts):
            if not l.startswith('  ts_state: '):
                return
            state = l[len('  ts_state: '):]
            if state in ('e', 'od', 'ud'):
                counts['remove'] += 1
            elif state in ('i', 'u'):
                counts['install'] += 1

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

                counts = {'install' : 0, 'remove' : 0}
                for l in data[pkgstart:]:
                    l = l.rstrip()
                    _pkg_counts(l, counts)

                # Check to see if all the packages are available..
                bad = ' '
                for l in data[pkgstart:]:
                    l = l.rstrip()
                    if _pkg_avail(l):
                        continue

                    bad = '*'
                    break

                # assert (counts['install'] + counts['remove']) == numpkgs
                current = '%s%s' % (bad, current)
                if not done:
                    pkgititle = _("Install")
                    pkgilen = utf8_width(pkgititle)
                    if pkgilen < 6:
                        pkgilen = 6
                    pkgititle = utf8_width_fill(pkgititle, pkgilen)

                    pkgetitle = _("Remove")
                    pkgelen = utf8_width(pkgetitle)
                    if pkgelen < 6:
                        pkgelen = 6
                    pkgetitle = utf8_width_fill(pkgetitle, pkgelen)
                    print "?? |", pkgititle, "|", pkgetitle, "|", _("Filename")
                    
                    done = True

                numipkgs = locale.format("%d", counts['install'], True)
                numipkgs = "%*s" % (pkgilen, numipkgs)
                numepkgs = locale.format("%d", counts['remove'], True)
                numepkgs = "%*s" % (pkgelen, numepkgs)
                print "%s | %s | %s | %s" % (current, numipkgs, numepkgs,
                                             os.path.basename(yumtx))
            return 0, [_('Saved transactions from %s; looked at %u files') %
                       (load_file, len(yumtxs))]

        self.doneCommand(base, _("loading transaction from %s") % load_file)
        
        base.load_ts(load_file)
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
        return "<repoid> <list|info|install|remove|upgrade|reinstall*|remove-or-*> [pkg(s)]"

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
        if len(extcmds) < 2: # <repoid> install|remove [pkgs]
            base.logger.critical(
                    _('Error: Need to pass a repoid. and command to %s') % basecmd)
            _err_mini_usage(base, basecmd)
            raise cli.CliError
        if extcmds[1] not in ('info', 'list'):
            checkRootUID(base)
        checkGPGKey(base)
        self.repoid = checkRepoPackageArg(base, basecmd, extcmds)

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

        repoid = self.repoid
        cmd = extcmds[1]
        args = extcmds[2:]
        noargs = False
        if not args:
            noargs = True
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
                 'upgrade-to' : 'update-to', # Hack, but meh.
                 'check-upgrade' : 'check-update', # Hack, but meh.
                 'check-upgrades' : 'check-update', # Hack, but meh.
                 'check-updates' : 'check-update', # Hack, but meh.
                 }
        cmd = remap.get(cmd, cmd)

        if False: pass
        elif cmd == 'list': # list/info is easiest...
            return ListCommand().doCommand(base, cmd, args, repoid=repoid)
        elif cmd == 'info':
            return InfoCommand().doCommand(base, cmd, args, repoid=repoid)
        elif cmd == 'check-update':
            return CheckUpdateCommand().doCommand(base, cmd, args,repoid=repoid)

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
                updateinfo.remove_txmbrs(base)
                return 2, P_('%d package to update', '%d packages to update',
                             num)

        elif cmd == 'update-to': # update is basically the same as install...
            for arg in args:
                txmbrs = base.update(pattern=arg, update_to=True, repoid=repoid)
                _add_repopkg2txmbrs(txmbrs, repoid)
                num += len(txmbrs)

            if num:
                updateinfo.remove_txmbrs(base)
                return 2, P_('%d package to update', '%d packages to update',
                             num)

        elif cmd in ('reinstall-old', 'reinstall-installed'):
            #  We have to choose for reinstall, for "reinstall foo" do we mean:
            # 1. reinstall the packages that are currently installed from "foo".
            # 2. reinstall the packages specified to the ones from "foo"

            # This is for installed.from_repo=foo
            if noargs:
                onot_found_a = base._not_found_a.copy()
            for arg in args:
                txmbrs = base.reinstall(pattern=arg,
                                        repoid=repoid, repoid_install=repoid)
                _add_repopkg2txmbrs(txmbrs, repoid)
                num += len(txmbrs)
            if noargs:
                base._not_found_a = onot_found_a

            if num:
                return 2, P_('%d package to reinstall',
                             '%d packages to reinstall', num)

        elif cmd in ('reinstall-new', 'reinstall-available', 'move-to'):
            # This is for move-to the packages from this repo.
            if noargs:
                onot_found_a = base._not_found_a.copy()
            for arg in args:
                txmbrs = base.reinstall(pattern=arg, repoid_install=repoid)
                _add_repopkg2txmbrs(txmbrs, repoid)
                num += len(txmbrs)
            if noargs:
                base._not_found_a = onot_found_a

            if num:
                return 2, P_('%d package to move to',
                             '%d packages to move to', num)

        elif cmd == 'reinstall':
            #  This means "guess", so doing the old version unless it produces
            # nothing, in which case try switching.
            if noargs:
                onot_found_a = base._not_found_a.copy()
            for arg in args:
                try:
                    txmbrs = base.reinstall(pattern=arg,
                                            repoid=repoid,repoid_install=repoid)
                except yum.Errors.ReinstallRemoveError:
                    continue
                _add_repopkg2txmbrs(txmbrs, repoid)
                num += len(txmbrs)
            if noargs:
                base._not_found_a = onot_found_a.copy()

            if num:
                return 2, P_('%d package to reinstall',
                             '%d packages to reinstall', num)

            for arg in args:
                txmbrs = base.reinstall(pattern=arg, repoid_install=repoid)
                _add_repopkg2txmbrs(txmbrs, repoid)
                num += len(txmbrs)
            if noargs:
                base._not_found_a = onot_found_a

            if num:
                return 2, P_('%d package to move to',
                             '%d packages to move to', num)

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
                    apkgs = []
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
            return InfoCommand().needTs(base, cmd, extcmds[2:])

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
        if cmd in ('check-update', 'check-upgrade',
                   'check-updates', 'check-upgrades'):
            return CheckUpdateCommand().cacheRequirement(base, cmd, extcmds[2:])
        return 'write'

# Using this a lot, so make it easier...
_upi = updateinfo
class UpdateinfoCommand(YumCommand):
    # Old command names...
    direct_cmds = {'list-updateinfo'    : 'list',
                   'list-security'      : 'list',
                   'list-sec'           : 'list',
                   'info-updateinfo'    : 'info',
                   'info-security'      : 'info',
                   'info-sec'           : 'info',
                   'summary-updateinfo' : 'summary'}

    #  Note that this code (instead of using inheritance and multiple
    # cmd classes) means that "yum help" only displays the updateinfo command.
    # Which is what we want, because the other commands are just backwards
    # compatible gunk we don't want the user using).
    def getNames(self):
        return ['updateinfo'] + sorted(self.direct_cmds.keys())

    def getUsage(self):
        return "[info|list|...] [security|...] [installed|available|all] [pkgs|id]"

    def getSummary(self):
        return "Acts on repository update information"

    def doCheck(self, base, basecmd, extcmds):
        pass

    def list_show_pkgs(self, base, md_info, list_type, show_type,
                       iname2tup, data, msg):
        n_maxsize = 0
        r_maxsize = 0
        t_maxsize = 0
        for (notice, pkgtup, pkg) in data:
            n_maxsize = max(len(notice['update_id']), n_maxsize)
            tn = notice['type']
            if tn == 'security' and notice['severity']:
                tn = notice['severity'] + '/Sec.'
            t_maxsize = max(len(tn),                  t_maxsize)
            if show_type:
                for ref in _upi._ysp_safe_refs(notice['references']):
                    if ref['type'] != show_type:
                        continue
                    r_maxsize = max(len(str(ref['id'])), r_maxsize)

        for (notice, pkgtup, pkg) in data:
            mark = ''
            if list_type == 'all':
                mark = '  '
                if pkgtup[0] in iname2tup and _upi._rpm_tup_vercmp(iname2tup[pkgtup[0]], pkgtup) >= 0:
                    mark = 'i '
            tn = notice['type']
            if tn == 'security' and notice['severity']:
                tn = notice['severity'] + '/Sec.'

            if show_type and _upi._ysp_has_info_md(show_type, notice):
                for ref in _upi._ysp_safe_refs(notice['references']):
                    if ref['type'] != show_type:
                        continue
                    msg("%s %-*s %-*s %s" % (mark, r_maxsize, str(ref['id']),
                                             t_maxsize, tn, pkg))
            elif hasattr(pkg, 'name'):
                print base.fmtKeyValFill("%s: " % pkg.name,
                                         base._enc(pkg.summary))
            else:
                msg("%s%-*s %-*s %s" % (mark, n_maxsize, notice['update_id'],
                                        t_maxsize, tn, pkg))

    def info_show_pkgs(self, base, md_info, list_type, show_type,
                       iname2tup, data, msg):
        show_pkg_info_done = {}
        for (notice, pkgtup, pkg) in data:
            if notice['update_id'] in show_pkg_info_done:
                continue
            show_pkg_info_done[notice['update_id']] = notice

            if hasattr(notice, 'text'):
                debug_log_lvl = yum.logginglevels.DEBUG_3
                vlog = base.verbose_logger
                if vlog.isEnabledFor(debug_log_lvl):
                    obj = notice.text(skip_data=[])
                else:
                    obj = notice.text()
            else:
                # Python-2.4.* doesn't understand str(x) returning unicode
                obj = notice.__str__()

            if list_type == 'all':
                if pkgtup[0] in iname2tup and _upi._rpm_tup_vercmp(iname2tup[pkgtup[0]], pkgtup) >= 0:
                    obj = obj + "\n  Installed : true"
                else:
                    obj = obj + "\n  Installed : false"
            msg(obj)

    def summary_show_pkgs(self, base, md_info, list_type, show_type,
                          iname2tup, data, msg):
        def _msg(x):
            base.verbose_logger.info("%s", x)
        counts = {}
        sev_counts = {}
        show_pkg_info_done = {}
        for (notice, pkgtup, pkg) in data:
            if notice['update_id'] in show_pkg_info_done:
                continue
            show_pkg_info_done[notice['update_id']] = notice
            counts[notice['type']] = counts.get(notice['type'], 0) + 1
            if notice['type'] == 'security':
                sev = notice['severity']
                if sev is None:
                    sev = ''
                sev_counts[sev] = sev_counts.get(sev, 0) + 1

        maxsize = 0
        for T in ('newpackage', 'security', 'bugfix', 'enhancement'):
            if T not in counts:
                continue
            size = len(str(counts[T]))
            if maxsize < size:
                maxsize = size
        if not maxsize:
            if base.conf.autocheck_running_kernel:
                _upi._check_running_kernel(base, md_info, _msg)
            return

        outT = {'newpackage' : 'New Package',
                'security' : 'Security',
                'bugfix' : 'Bugfix',
                'enhancement' : 'Enhancement'}
        print "Updates Information Summary:", list_type
        for T in ('newpackage', 'security', 'bugfix', 'enhancement'):
            if T not in counts:
                continue
            n = outT[T]
            if T == 'security' and len(sev_counts) == 1:
                sn = sev_counts.keys()[0]
                if sn != '':
                    n = sn + " " + n
            print "    %*u %s notice(s)" % (maxsize, counts[T], n)
            if T == 'security' and len(sev_counts) != 1:
                def _sev_sort_key(key):
                    # We want these in order, from "highest" to "lowest".
                    # Anything unknown is "higher". meh.
                    return {'Critical' : "zz1",
                            'Important': "zz2",
                            'Moderate' : "zz3",
                            'Low'      : "zz4",
                            }.get(key, key)

                for sn in sorted(sev_counts, key=_sev_sort_key):
                    args = (maxsize, sev_counts[sn],sn or '?', outT['security'])
                    print "        %*u %s %s notice(s)" % args
        if base.conf.autocheck_running_kernel:
            _upi._check_running_kernel(base, md_info, _msg)
        self.show_pkg_info_done = {}

    def _get_new_pkgs(self, md_info):
        for notice in md_info.notices:
            if notice['type'] != "newpackage":
                continue
            for upkg in notice['pkglist']:
                for pkg in upkg['packages']:
                    pkgtup = (pkg['name'], pkg['arch'], pkg['epoch'] or '0',
                              pkg['version'], pkg['release'])
                    yield (notice, pkgtup)

    _cmd2filt = {"bugzillas" : "bugzilla",
                 "bugzilla" : "bugzilla",
                 "bzs" : "bugzilla",
                 "bz" : "bugzilla",

                 "sec" : "security",

                 "cves" : "cve",
                 "cve" : "cve",

                 "newpackages" : "newpackage",
                 "new-packages" : "newpackage",
                 "newpackage" : "newpackage",
                 "new-package" : "newpackage",
                 "new" : "newpackage"}
    for filt_type in _upi._update_info_types_:
        _cmd2filt[filt_type] = filt_type

    def doCommand(self, base, basecmd, extcmds):
        if basecmd in self.direct_cmds:
            subcommand = self.direct_cmds[basecmd]
        elif extcmds and extcmds[0] in ('list', 'info', 'summary',
                                        'remove-pkgs-ts', 'exclude-updates',
                                        'exclude-all',
                                        'check-running-kernel'):
            subcommand = extcmds[0]
            extcmds = extcmds[1:]
        elif extcmds and extcmds[0] in self._cmd2filt:
            subcommand = 'list'
        elif extcmds:
            subcommand = 'info'
        else:
            subcommand = 'summary'

        if subcommand == 'list':
            return self.doCommand_li(base, 'updateinfo list', extcmds,
                                     self.list_show_pkgs)
        if subcommand == 'info':
            return self.doCommand_li(base, 'updateinfo info', extcmds,
                                     self.info_show_pkgs)

        if subcommand == 'summary':
            return self.doCommand_li(base, 'updateinfo summary', extcmds,
                                     self.summary_show_pkgs)

        if subcommand == 'remove-pkgs-ts':
            filters = None
            if extcmds:
                filters = updateinfo._args2filters(extcmds)
            updateinfo.remove_txmbrs(base, filters)
            return 0, [basecmd + ' ' + subcommand + ' done']

        if subcommand == 'exclude-all':
            filters = None
            if extcmds:
                filters = updateinfo._args2filters(extcmds)
            updateinfo.exclude_all(base, filters)
            return 0, [basecmd + ' ' + subcommand + ' done']

        if subcommand == 'exclude-updates':
            filters = None
            if extcmds:
                filters = updateinfo._args2filters(extcmds)
            updateinfo.exclude_updates(base, filters)
            return 0, [basecmd + ' ' + subcommand + ' done']

        if subcommand == 'check-running-kernel':
            def _msg(x):
                base.verbose_logger.info("%s", x)
            updateinfo._check_running_kernel(base, base.upinfo, _msg)
            return 0, [basecmd + ' ' + subcommand + ' done']

    def doCommand_li_new(self, base, list_type, extcmds, md_info, msg,
                         show_pkgs, iname2tup):
        done_pkgs = set()
        data = []
        for (notice, pkgtup) in sorted(self._get_new_pkgs(md_info),
                                       key=lambda x: x[1][0]):
            if extcmds and not _upi._match_sec_cmd(extcmds, pkgtup[0], notice):
                continue
            n = pkgtup[0]
            if n in done_pkgs:
                continue
            ipkgs = list(reversed(sorted(base.rpmdb.searchNames([n]))))
            if list_type in ('installed', 'updates') and not ipkgs:
                done_pkgs.add(n)
                continue
            if list_type == 'available' and ipkgs:
                done_pkgs.add(n)
                continue

            pkgs = base.pkgSack.searchPkgTuple(pkgtup)
            if not pkgs:
                continue
            if list_type == "updates" and pkgs[0].verLE(ipkgs[0]):
                done_pkgs.add(n)
                continue
            done_pkgs.add(n)
            data.append((notice, pkgtup, pkgs[0]))
        show_pkgs(base, md_info, list_type, None, iname2tup, data, msg)

    def _parse_extcmds(self, extcmds):
        filt_type = None
        show_type = None
        if len(extcmds) >= 1:
            filt_type = None
            
            if extcmds[0] in self._cmd2filt:
                filt_type = self._cmd2filt[extcmds.pop(0)]
            show_type = filt_type
            if filt_type and filt_type in _upi._update_info_types_:
                show_type = None
        return extcmds, show_type, filt_type

    def doCommand_li(self, base, basecmd, extcmds, show_pkgs):
        md_info = base.upinfo
        def msg(x):
            #  Don't use: logger.log(logginglevels.INFO_2, x)
            # or -q deletes everything.
            print x

        opts = _upi._updateinfofilter2opts(base.updateinfo_filters)
        extcmds, show_type, filt_type = self._parse_extcmds(extcmds)

        list_type = "available"
        if extcmds and extcmds[0] in ("updates","available","installed", "all"):
            list_type = extcmds.pop(0)
            if filt_type is None:
                extcmds, show_type, filt_type = self._parse_extcmds(extcmds)

        opts.sec_cmds = extcmds
        used_map = _upi._ysp_gen_used_map(base.updateinfo_filters)
        iname2tup = {}
        if False: pass
        elif list_type in ('installed', 'all'):
            name2tup = _upi._get_name2allpkgtup(base)
            iname2tup = _upi._get_name2instpkgtup(base)
        elif list_type == 'updates':
            name2tup = _upi._get_name2oldpkgtup(base)
        elif list_type == 'available':
            name2tup = _upi._get_name2instpkgtup(base)

        if filt_type == "newpackage":
            self.doCommand_li_new(base, list_type, extcmds, md_info, msg,
                                  show_pkgs, iname2tup)
            return 0, [basecmd + ' new done']

        def _show_pkgtup(pkgtup):
            name = pkgtup[0]
            notices = reversed(md_info.get_applicable_notices(pkgtup))
            for (pkgtup, notice) in notices:
                if filt_type and not _upi._ysp_has_info_md(filt_type, notice):
                    continue

                if list_type == 'installed':
                    # Remove any that are newer than what we have installed
                    if _upi._rpm_tup_vercmp(iname2tup[name], pkgtup) < 0:
                        continue

                if _upi._ysp_should_filter_pkg(opts, name, notice, used_map):
                    yield (pkgtup, notice)

        data = []
        for pkgname in sorted(name2tup):
            for (pkgtup, notice) in _show_pkgtup(name2tup[pkgname]):
                d = {}
                (d['n'], d['a'], d['e'], d['v'], d['r']) = pkgtup
                if d['e'] == '0':
                    d['epoch'] = ''
                else:
                    d['epoch'] = "%s:" % d['e']
                data.append((notice, pkgtup,
                            "%(n)s-%(epoch)s%(v)s-%(r)s.%(a)s" % d))
        show_pkgs(base, md_info, list_type, show_type, iname2tup, data, msg)

        _upi._ysp_chk_used_map(used_map, msg)

        return 0, [basecmd + ' done']


class UpdateMinimalCommand(YumCommand):
    def getNames(self):
        return ['update-minimal', 'upgrade-minimal',
                'minimal-update', 'minimal-upgrade']

    def getUsage(self):
        return "[PACKAGE-wildcard]"

    def getSummary(self):
        return _("Works like upgrade, but goes to the 'newest' package match which fixes a problem that affects your system")

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

        num = len(base.tsInfo)
        _upi.update_minimal(base, extcmds)
        num = len(base.tsInfo) - num
        
        if num > 0:
            msg = '%d packages marked for minimal Update' % num
            return 2, [msg]
        else:
            return 0, ['No Packages marked for minimal Update']


class FSSnapshotCommand(YumCommand):
    def getNames(self):
        return ['fssnapshot', 'fssnap']

    def getUsage(self):
        return "[]"

    def getSummary(self):
        return _("Creates filesystem snapshots, or lists/deletes current snapshots.")

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

    @staticmethod
    def _li_snaps(base, snaps):
        snaps = sorted(snaps, key=lambda x: x['dev'])

        max_dev = utf8_width(_('Snapshot'))
        max_ori = utf8_width(_('Origin'))
        for data in snaps:
            max_dev = max(max_dev, len(data['dev']))
            max_ori = max(max_ori, len(data['origin']))

        done = False
        for data in snaps:
            if not done:
                print ("%s %s %s %s %s %s" %
                       (utf8_width_fill(_('Snapshot'), max_dev),
                        utf8_width_fill(_('Size'), 6, left=False),
                        utf8_width_fill(_('Used'), 6, left=False),
                        utf8_width_fill(_('Free'), 6, left=False),
                        utf8_width_fill(_('Origin'), max_ori), _('Tags')))
                done = True
            print ("%*s %6s %5.1f%% %6s %*s %s" %
                   (max_dev, data['dev'], base.format_number(data['size']),
                    data['used'],
                    base.format_number(data['free']),
                    max_ori, data['origin'], ",".join(data['tags'])))

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
        if extcmds and extcmds[0] in ('list', 'delete', 'create', 'summary',
                                      'have-space', 'has-space'):
            subcommand = extcmds[0]
            extcmds = extcmds[1:]
        else:
            subcommand = 'summary'

        if not base.fssnap.available:
            if not base.rpmdb.searchNames(['python-lvm']):
                print _("Snapshot support not available, no python-lvm package installed.")
            else:
                print _("Snapshot support not available, python-lvm is old/broken.")
            return 0, [basecmd + ' ' + subcommand + ' done']

        if subcommand == 'list':
            snaps = base.fssnap.old_snapshots()
            print _("List of %u snapshosts:") % len(snaps)
            self._li_snaps(base, snaps)

        if subcommand == 'delete':
            snaps = base.fssnap.old_snapshots()
            devs = [x['dev'] for x in snaps]
            snaps = set()
            for dev in devs:
                if dev in snaps:
                    continue

                for extcmd in extcmds:
                    if dev == extcmd or fnmatch.fnmatch(dev, extcmd):
                        snaps.add(dev)
                        break
            snaps = base.fssnap.del_snapshots(devices=snaps)
            print _("Deleted %u snapshosts:") % len(snaps)
            self._li_snaps(base, snaps)

        if subcommand in ('have-space', 'has-space'):
            pc = base.conf.fssnap_percentage
            if base.fssnap.has_space(pc):
                print _("Space available to take a snapshot.")
            else:
                print _("Not enough space available to take a snapshot.")

        if subcommand == 'create':
            tags = {'*': ['reason=manual']}
            pc = base.conf.fssnap_percentage
            for (odev, ndev) in base.fssnap.snapshot(pc, tags=tags):
                print _("Created snapshot from %s, results is: %s") %(odev,ndev)
            else:
                print _("Failed to create snapshots")

        if subcommand == 'summary':
            snaps = base.fssnap.old_snapshots()
            if not snaps:
                print _("No snapshots, LVM version:"), base.fssnap.version
                return 0, [basecmd + ' ' + subcommand + ' done']

            used = 0
            dev_oris = set()
            for snap in snaps:
                used += snap['used']
                dev_oris.add(snap['origin_dev'])

            msg = _("Have %u snapshots, using %s space, from %u origins.")
            print msg % (len(snaps), base.format_number(used), len(dev_oris))

        return 0, [basecmd + ' ' + subcommand + ' done']


class FSCommand(YumCommand):
    def getNames(self):
        return ['fs']

    def getUsage(self):
        return "[]"

    def getSummary(self):
        return _("Acts on the filesystem data of the host, mainly for removing docs/lanuages for minimal hosts.")

    def doCheck(self, base, basecmd, extcmds):
        """Verify that conditions are met so that this command can run.
        These include that the program is being run by the root user,
        that there are enabled repositories with gpg keys, and that
        this command is called with appropriate arguments.

        :param base: a :class:`yum.Yumbase` object
        :param basecmd: the name of the command
        :param extcmds: the command line arguments passed to *basecmd*
        """
        if extcmds and extcmds[0] in ('du', 'status', 'diff'):
            # Anyone can go for it...
            return

        if len(extcmds) == 1 and extcmds[0] in ('filters', 'filter'):
            # Can look, but not touch.
            return

        checkRootUID(base)

    def _fs_pkg_walk(self, pkgs, prefix, modified=False, verbose=False):

        pfr = {'norm' : {},
               'mod' : {},
               'ghost' : {},
               'miss' : {},
               'not' : {}
               }

        def quick_match(pkgs):
            for pkg in pkgs:
                for fname in pkg.filelist + pkg.dirlist:
                    if not fname.startswith(prefix):
                        continue
                    pfr['norm'][fname] = pkg
                for fname in pkg.ghostlist:
                    if not fname.startswith(prefix):
                        continue
                    pfr['ghost'][fname] = pkg
            return pfr

        def _quick_match_iter(pkgs):
            # Walking the fi information is much slower than filelist/dirlist
            for pkg in pkgs:
                found = False
                for fname in pkg.dirlist:
                    if fname.startswith(prefix):
                        yield pkg
                        found = True
                        break
                if found:
                    continue
                for fname in pkg.filelist:
                    if fname.startswith(prefix):
                        yield pkg
                        found = True
                        break
                if found:
                    continue
                for fname in pkg.ghostlist:
                    if fname.startswith(prefix):
                        yield pkg
                        break

        def verify_match(pkgs):
            _pfs = []
            def scoop_pfs(pfs):
                _pfs.append(pfs)

                if not modified:
                    return []

                return pfs

            if prefix != '/':
                pkgs = _quick_match_iter(pkgs)
            for pkg in pkgs:
                _pfs = []
                probs = pkg.verify(patterns=[prefix+'*'], fake_problems=False,
                                   callback=scoop_pfs, failfast=True)

                for pf in _pfs[0]:
                    if pf.filename in probs:
                        pfr['mod'][pf.filename] = pkg
                    elif pf.rpmfile_state == 'not installed':
                        pfr['not'][pf.filename] = pkg
                    elif 'ghost' in pf.rpmfile_types:
                        pfr['ghost'][pf.filename] = pkg
                    elif 'missing ok' in pf.rpmfile_types:
                        pfr['miss'][pf.filename] = pkg
                    else:
                        pfr['norm'][pf.filename] = pkg
            return pfr

        # return quick_match(pkgs)
        return verify_match(pkgs)

    def _fs_du(self, base, extcmds):
        def _dir_prefixes(path):
            while path != '/':
                path = os.path.dirname(path)
                yield path

        def loc_num(x):
            """ String of a number in the readable "locale" format. """
            return locale.format("%d", int(x), True)

        data = {'pkgs_size' : {},
                'pkgs_not_size' : {},
                'pkgs_ghost_size' : {},
                'pkgs_miss_size' : {},
                'pkgs_mod_size' : {},

                'pres_size' : {},
                'data_size' : {},
                'data_not_size' : {},

                'pkgs_count' : 0,
                'pkgs_not_count' : 0,
                'pkgs_ghost_count' : 0,
                'pkgs_miss_count' : 0,
                'pkgs_mod_count' : 0,

                'data_count' : 0} # data_not_count == pkgs_not_count

        def _add_size(d, v, size):
            if v not in d:
                d[v] = 0
            d[v] += size

        def deal_with_file(fpath, need_prefix=True):
            size = os.path.getsize(fpath)
            if fpath in pfr['norm']:
                data['pkgs_count'] += size
                _add_size(data['pkgs_size'], pfr['norm'][fpath], size)
            elif fpath in pfr['ghost']:
                data['pkgs_ghost_count'] += size
                _add_size(data['pkgs_ghost_size'], pfr['ghost'][fpath], size)
            elif fpath in pfr['not']:
                data['pkgs_not_count'] += size
                _add_size(data['pkgs_not_size'], pfr['not'][fpath], size)
                data['data_not_size'][fpath] = size
            elif fpath in pfr['miss']:
                data['pkgs_miss_count'] += size
                _add_size(data['pkgs_miss_size'], pfr['miss'][fpath], size)
            elif fpath in pfr['mod']:
                data['pkgs_mod_count'] += size
                _add_size(data['pkgs_mod_size'], pfr['mod'][fpath], size)
            elif need_prefix and False:
                for fpre_path in _dir_prefixes(fpath):
                    if fpre_path not in pkg_files:
                        continue
                    _add_size(data['pres_size'], pkg_files[fpre_path], size)
                    break
                data['data_count'] += size
                data['data_size'][fpath] = size
            else:
                data['data_count'] += size
                data['data_size'][fpath] = size

        prefix = "."
        if extcmds:
            prefix = extcmds[0]
            extcmds = extcmds[1:]

        if not os.path.exists(prefix):
            return 1, [_('No such file or directory: ' + prefix)]

        max_show_len = 4
        if extcmds:
            try:
                max_show_len = int(extcmds[0])
            except:
                pass

        verbose = base.verbose_logger.isEnabledFor(logginglevels.DEBUG_3)

        pfr = self._fs_pkg_walk(base.rpmdb, prefix, verbose=verbose)

        base.closeRpmDB() # C-c ftw.

        num = 0
        if os.path.isfile(prefix):
            num += 1
            deal_with_file(prefix)

        for root, dirs, files in os.walk(prefix):
            for fname in files:
                num += 1
                fpath = os.path.normpath(root + '/' + fname)
                if os.path.islink(fpath):
                    continue

                deal_with_file(fpath, need_prefix=verbose)

        # output
        print "Files            :", loc_num(num)
        tot = 0
        tot += data['pkgs_count']
        tot += data['pkgs_ghost_count']
        tot += data['pkgs_not_count']
        tot += data['pkgs_miss_count']
        tot += data['pkgs_mod_count']
        tot += data['data_count']
        print "Total size       :", base.format_number(tot)
        if not tot:
            return

        num = data['pkgs_count']
        if not verbose:
            num += data['pkgs_ghost_count']
            num += data['pkgs_miss_count']
            num += data['pkgs_mod_count']
        print "       Pkgs size :", "%-5s" % base.format_number(num),
        print "(%3.0f%%)" % ((num * 100.0) / tot)
        if verbose:
            for (title, num) in ((_(" Ghost pkgs size :"),
                                  data['pkgs_ghost_count']),
                                 (_(" Not pkgs size :"),
                                  data['pkgs_not_count']),
                                 (_(" Miss pkgs size :"),
                                  data['pkgs_miss_count']),
                                 (_(" Mod. pkgs size :"),
                                  data['pkgs_mod_count'])):
                if not num:
                    continue
                print title, "%-5s" % base.format_number(num),
                print "(%3.0f%%)" % ((num * 100.0) / tot)
        num = data['data_count']
        if not verbose:
            num += data['pkgs_not_count']
        print _("       Data size :"), "%-5s" % base.format_number(num),
        print "(%3.0f%%)" % ((num * 100.0) / tot)
        if verbose:
            print ''
            print _("Pkgs       :"), loc_num(len(data['pkgs_size']))
            print _("Ghost Pkgs :"), loc_num(len(data['pkgs_ghost_size']))
            print _("Not Pkgs   :"), loc_num(len(data['pkgs_not_size']))
            print _("Miss. Pkgs :"), loc_num(len(data['pkgs_miss_size']))
            print _("Mod. Pkgs  :"), loc_num(len(data['pkgs_mod_size']))

        def _pkgs(p_size, msg):
            tot = min(max_show_len, len(p_size))
            if tot:
                print ''
                print msg % tot
            num = 0
            for pkg in sorted(p_size, key=lambda x: p_size[x], reverse=True):
                num += 1
                print _("%*d. %60s %-5s") % (len(str(tot)), num, pkg,
                                             base.format_number(p_size[pkg]))
                if num >= tot:
                    break

        if verbose:
            _pkgs(data['pkgs_size'], _('Top %d packages:'))
            _pkgs(data['pkgs_ghost_size'], _('Top %d ghost packages:'))
            _pkgs(data['pkgs_not_size'], _('Top %d not. packages:'))
            _pkgs(data['pkgs_miss_size'], _('Top %d miss packages:'))
            _pkgs(data['pkgs_mod_size'], _('Top %d mod. packages:'))
            _pkgs(data['pres_size'], _('Top %d prefix packages:'))
        else:
            tmp = {}
            tmp.update(data['pkgs_size'])
            for d in data['pkgs_ghost_size']:
                _add_size(tmp, d, data['pkgs_ghost_size'][d])
            for d in data['pkgs_miss_size']:
                _add_size(tmp, d, data['pkgs_miss_size'][d])
            for d in data['pkgs_mod_size']:
                _add_size(tmp, d, data['pkgs_mod_size'][d])
            _pkgs(tmp, _('Top %d packages:'))

        print ''
        if verbose:
            data_size = data['data_size']
        else:
            data_size = {}
            data_size.update(data['data_size'])
            data_size.update(data['data_not_size'])

        tot = min(max_show_len, len(data_size))
        if tot:
            print _('Top %d non-package files:') % tot
        num = 0
        for fname in sorted(data_size,
                            key=lambda x: data_size[x],
                            reverse=True):
            num += 1
            dsznum = data_size[fname]
            print _("%*d. %60s %-5s") % (len(str(tot)), num, fname,
                                         base.format_number(dsznum))
            if num >= tot:
                break

    def _fs_filters(self, base, extcmds):
        def _save(confkey):
            writeRawConfigFile = yum.config._writeRawConfigFile

            # Always create installroot, so we can change it.
            if not os.path.exists(base.conf.installroot + '/etc/yum'):
                os.makedirs(base.conf.installroot + '/etc/yum')

            fn = base.conf.installroot+'/etc/yum/yum.conf'
            if not os.path.exists(fn):
                # Try the old default
                nfn = base.conf.installroot+'/etc/yum.conf'
                if os.path.exists(nfn):
                    fn = nfn
                else:
                    shutil.copy2(base.conf.config_file_path, fn)
            ybc = base.conf
            writeRawConfigFile(fn, 'main', ybc.yumvar,
                               ybc.cfg.options, ybc.iteritems,
                               ybc.optionobj,
                               only=[confkey])

        if not extcmds:
            oil = base.conf.override_install_langs
            if not oil:
                oil = "rpm: " + rpm.expandMacro("%_install_langs")
            print _("File system filters:")
            print _("  Nodocs:"), 'nodocs' in base.conf.tsflags
            print _("  Languages:"), oil
        elif extcmds[0] in ('docs', 'nodocs',
                            'documentation', 'nodocumentation'):
            c_f = 'nodocs' in base.conf.tsflags
            n_f = not extcmds[0].startswith('no')
            if n_f == c_f:
                if n_f:
                    print _("Already enabled documentation filter.")
                else:
                    print _("Already disabled documentation filter.")
                return

            if n_f:
                print _("Enabling documentation filter.")
            else:
                print _("Disabling documentation filter.")

            nts = base.conf.tsflags
            if n_f:
                nts = nts + ['nodocs']
            else:
                nts = [x for x in nts if x != 'nodocs']
            base.conf.tsflags = " ".join(nts)

            _save('tsflags')

        elif extcmds[0] in ('langs', 'nolangs', 'lang', 'nolang',
                            'languages', 'nolanguages',
                            'language', 'nolanguage'):
            if extcmds[0].startswith('no') or len(extcmds) < 2 or 'all' in extcmds:
                val = 'all'
            else:
                val = ":".join(extcmds[1:])

            if val == base.conf.override_install_langs:
                if val:
                    print _("Already filtering languages to: %s") % val
                else:
                    print _("Already disabled language filter.")
                return

            if val:
                print _("Setting language filter to: %s") % val
            else:
                print _("Disabling language filter.")

            base.conf.override_install_langs = val

            _save('override_install_langs')

        else:
            return 1, [_('Not a valid sub-command of fs filter')]

    def _fs_refilter(self, base, extcmds):
        c_f = 'nodocs' in base.conf.tsflags
        # FIXME: C&P from init.
        oil = base.conf.override_install_langs
        if not oil:
            oil = rpm.expandMacro("%_install_langs")
        if oil == 'all':
            oil = ''
        elif oil:
            oil = ":".join(sorted(oil.split(':')))

        found = False
        num = 0
        for pkg in base.rpmdb.returnPackages(patterns=extcmds):
            if False: pass
            elif oil != pkg.yumdb_info.get('ts_install_langs', ''):
                txmbrs = base.reinstall(po=pkg)
                num += len(txmbrs)
            elif c_f != ('true' == pkg.yumdb_info.get('tsflag_nodocs')):
                txmbrs = base.reinstall(po=pkg)
                num += len(txmbrs)
            else:
                found = True

        if num:
            return 2,P_('%d package to reinstall','%d packages to reinstall',
                        num)

        if not found:
            return 1, [_('No valid packages: %s') % " ".join(extcmds)]

    def _fs_refilter_cleanup(self, base, extcmds):
        pkgs = base.rpmdb.returnPackages(patterns=extcmds)

        verbose = base.verbose_logger.isEnabledFor(logginglevels.DEBUG_3)

        pfr = self._fs_pkg_walk(pkgs, "/", verbose=verbose, modified=True)

        base.closeRpmDB() # C-c ftw.

        for fname in sorted(pfr['not']):
            print _('Removing:'), fname
            try: # Ignore everything, unlink_f() doesn't.
                os.unlink(fname)
            except OSError, e:
                if e.errno == errno.EISDIR:
                    try:
                        os.rmdir(fname)
                    except:
                        pass
            except:
                pass

    def _fs_diff(self, base, extcmds):
        def deal_with_file(fpath):
            if fpath in pfr['norm']:
                pass
            elif fpath in pfr['ghost']:
                pass
            elif fpath in pfr['not']:
                print >>sys.stderr, _('Not installed:'), fpath
            elif fpath in pfr['miss']:
                pass
            elif fpath in pfr['mod']:
                pkg = apkgs[pfr['mod'][fpath].pkgtup]
                # Hacky ... but works.
                sys.stdout.flush()
                extract_cmd = "cd %s; rpm2cpio %s | cpio --quiet -id .%s"
                extract_cmd =  extract_cmd % (tmpdir, pkg.localPkg(), fpath)
                os.system(extract_cmd)
                diff_cmd = "diff -ru %s %s" % (tmpdir + fpath, fpath)
                print diff_cmd
                sys.stdout.flush()
                os.system(diff_cmd)
            else:
                print >>sys.stderr, _('Not packaged?:'), fpath

        if not distutils.spawn.find_executable("diff"):
            raise yum.Errors.YumBaseError, _("Can't find diff command")
        # These just shouldn't happen...
        if not distutils.spawn.find_executable("cpio"):
            raise yum.Errors.YumBaseError, _("Can't find cpio command")
        if not distutils.spawn.find_executable("rpm2cpio"):
            raise yum.Errors.YumBaseError, _("Can't find rpm2cpio command")

        prefix = "."
        if extcmds:
            prefix = extcmds[0]
            extcmds = extcmds[1:]

        pkgs = base.rpmdb.returnPackages(patterns=extcmds)

        verbose = base.verbose_logger.isEnabledFor(logginglevels.DEBUG_3)

        pfr = self._fs_pkg_walk(pkgs, prefix, verbose=verbose, modified=True)

        base.closeRpmDB() # C-c ftw.

        apkgs = {}
        downloadpkgs = []
        for ipkg in set(pfr['mod'].values()):
            iyi = ipkg.yumdb_info
            if 'from_repo' in iyi: # Updates-testing etc.
                if iyi.from_repo in base.repos.repos:
                    repo = base.repos.getRepo(iyi.from_repo)
                    if not repo.isEnabled():
                        base.repos.enableRepo(repo.id)

            for apkg in base.pkgSack.searchPkgTuple(ipkg.pkgtup):
                if ('checksum_type' in iyi and
                    'checksum_data' in iyi and
                    iyi.checksum_type == apkg.checksum_type and
                    iyi.checksum_data == apkg.pkgId):
                    apkgs[ipkg.pkgtup] = apkg
                    downloadpkgs.append(apkg)
                    break
            if ipkg.pkgtup not in apkgs:
                raise yum.Errors.YumBaseError, _("Can't find package: %s") %ipkg

        if downloadpkgs:
            tmpdir = tempfile.mkdtemp()
            problems = base.downloadPkgs(downloadpkgs, callback_total=base.download_callback_total_cb) 
            if len(problems) > 0:
                errstring = ''
                errstring += _('Error downloading packages:\n')
                for key in problems:
                    errors = yum.misc.unique(problems[key])
                    for error in errors:
                        errstring += '  %s: %s\n' % (key, error)
                raise yum.Errors.YumBaseError, errstring

        for root, dirs, files in os.walk(prefix):
            for fname in files:
                fpath = os.path.normpath(root + '/' + fname)
                if os.path.islink(fpath):
                    continue

                deal_with_file(fpath)

        if downloadpkgs:
            shutil.rmtree(tmpdir)

    def _fs_status(self, base, extcmds):
        def deal_with_file(fpath):
            if fpath in pfr['norm']:
                pass
            elif fpath in pfr['ghost']:
                pass
            elif fpath in pfr['not']:
                print _('Not installed:'), fpath
            elif fpath in pfr['miss']:
                pass
            elif fpath in pfr['mod']:
                print _('Modified:'), fpath
            else:
                print _('Not packaged?:'), fpath

        prefix = "."
        if extcmds:
            prefix = extcmds[0]
            extcmds = extcmds[1:]

        pkgs = base.rpmdb.returnPackages(patterns=extcmds)

        verbose = base.verbose_logger.isEnabledFor(logginglevels.DEBUG_3)

        pfr = self._fs_pkg_walk(pkgs, prefix, verbose=verbose, modified=True)

        base.closeRpmDB() # C-c ftw.

        for root, dirs, files in os.walk(prefix):
            for fname in files:
                fpath = os.path.normpath(root + '/' + fname)
                if os.path.islink(fpath):
                    continue

                deal_with_file(fpath)

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
        if extcmds and extcmds[0] in ('filters', 'filter',
                                      'refilter', 'refilter-cleanup',
                                      'du', 'status', 'diff', 'snap'):
            subcommand = extcmds[0]
            extcmds = extcmds[1:]
        else:
            subcommand = 'filters'

        if False: pass

        elif subcommand == 'du':
            ret = self._fs_du(base, extcmds)

        elif subcommand in ('filter', 'filters'):
            ret = self._fs_filters(base, extcmds)

        elif subcommand == 'refilter':
            ret = self._fs_refilter(base, extcmds)

        elif subcommand == 'refilter-cleanup':
            ret = self._fs_refilter_cleanup(base, extcmds)

        elif subcommand == 'diff':
            ret = self._fs_diff(base, extcmds)

        elif subcommand == 'status':
            ret = self._fs_status(base, extcmds)

        elif subcommand == 'snap':
            ret = FSSnapshotCommand().doCommand(base, 'fs snap', args)

        else:
            return 1, [_('Not a valid sub-command of %s') % basecmd]

        if ret is not None:
            return ret

        return 0, [basecmd + ' ' + subcommand + ' done']
