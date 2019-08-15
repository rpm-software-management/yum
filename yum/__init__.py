#!/usr/bin/python -tt
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
# Copyright 2005 Duke University

"""
The Yum RPM software updater.
"""

import os
import os.path
import rpm
import sys

def _rpm_ver_atleast(vertup):
    """ Check if rpm is at least the current vertup. Can return False/True/None
        as rpm hasn't had version info for a long time. """
    if not hasattr(rpm, '__version_info__'):
        return None
    try:
        # 4.8.x rpm used strings for the tuple members, so convert.
        vi = tuple([ int(num) for num in rpm.__version_info__])
        return vi >= vertup
    except:
        return None # Something went wrong...

import re
import types
import errno
import time
import glob
import fnmatch
import logging
import logging.config
import operator
import tempfile
import shutil

import yum.i18n
# This is required to make gaftonmode work...
_wrap_yum_i18n__ = yum.i18n._
def _(*args, **kwargs):
    return _wrap_yum_i18n__(*args, **kwargs)
_wrap_yum_i18n_P_ = yum.i18n.P_
def P_(*args, **kwargs):
    return _wrap_yum_i18n_P_(*args, **kwargs)

import config
from config import ParsingError, ConfigParser
import Errors
import rpmsack
import rpmUtils.updates
from rpmUtils.arch import archDifference, canCoinstall, ArchStorage, isMultiLibArch
from rpmUtils.miscutils import compareEVR
import rpmUtils.transaction
import comps
import pkgtag_db
from repos import RepoStorage
import misc
from parser import ConfigPreProcessor, varReplace
import transactioninfo
import urlgrabber
from urlgrabber.grabber import URLGrabber, URLGrabError
from urlgrabber.progress import format_number
from packageSack import packagesNewestByName, packagesNewestByNameArch, ListPackageSack
import depsolve
import plugins
import logginglevels
import yumRepo
import callbacks
import yum.history
import yum.fssnapshots
from yum.fssnapshots import LibLVMError, lvmerr2str
import yum.igroups
import update_md

import warnings
warnings.simplefilter("ignore", Errors.YumFutureDeprecationWarning)

from packages import parsePackages, comparePoEVR
from packages import YumAvailablePackage, YumLocalPackage, YumInstalledPackage
from packages import YumUrlPackage, YumNotFoundPackage
from constants import *
from yum.rpmtrans import RPMTransaction,SimpleCliCallBack
from yum.i18n import to_unicode, to_str, exception2msg
from yum.drpm import DeltaInfo, DeltaPackage

import StringIO

from weakref import proxy as weakref

from urlgrabber.grabber import default_grabber

try:
    import cashe
except ImportError:
    cashe = None

__version__ = '3.4.3'
__version_info__ = tuple([ int(num) for num in __version__.split('.')])

#  Setup a default_grabber UA here that says we are yum, done using the global
# so that other API users can easily add to it if they want.
#  Don't do it at init time, or we'll get multiple additions if you create
# multiple YumBase() objects.
default_grabber.opts.user_agent += " yum/" + __version__


class _YumPreBaseConf:
    """This is the configuration interface for the :class:`YumBase`
    configuration.  To change configuration settings such as whether
    plugins are on or off, or the value of debuglevel, change the
    values here. Later, when :func:`YumBase.conf` is first called, all
    of the options will be automatically configured.
    """
    def __init__(self):
        self.fn = '/etc/yum/yum.conf'
        self.root = '/'
        self.init_plugins = True
        self.plugin_types = (plugins.TYPE_CORE,)
        self.optparser = None
        self.debuglevel = None
        self.errorlevel = None
        self.disabled_plugins = None
        self.enabled_plugins = None
        self.syslog_ident = None
        self.syslog_facility = None
        self.syslog_device = None
        self.arch = None
        self.releasever = None
        self.uuid = None


class _YumPreRepoConf:
    """This is the configuration interface for the repos configuration
    configuration.  To change configuration settings such what
    callbacks are used, change the values here. Later, when
    :func:`YumBase.repos` is first called, all of the options will be
    automatically configured.
    """
    def __init__(self):
        self.progressbar = None
        self.multi_progressbar = None
        self.callback = None
        self.failure_callback = None
        self.interrupt_callback = None
        self.confirm_func = None
        self.gpg_import_func = None
        self.gpgca_import_func = None
        self.cachedir = None
        self.cache = None


class _YumCostExclude:
    """ This excludes packages that are in repos. of lower cost than the passed
        repo. """

    def __init__(self, repo, repos):
        self.repo   = weakref(repo)
        self._repos = weakref(repos)

    def __contains__(self, pkgtup):
        # (n, a, e, v, r) = pkgtup
        for repo in self._repos.listEnabled():
            if repo.cost >= self.repo.cost:
                break
            #  searchNevra is a bit slower, although more generic for repos. 
            # that don't use sqlitesack as the backend ... although they are
            # probably screwed anyway.
            #
            # if repo.sack.searchNevra(n, e, v, r, a):
            if pkgtup in repo.sack._pkgtup2pkgs:
                return True
        return False

class YumBase(depsolve.Depsolve):
    """This is a primary structure and base class. It houses the
    objects and methods needed to perform most things in yum. It is
    almost an abstract class in that you will need to add your own
    class above it for most real use.
    """
    def __init__(self):
        depsolve.Depsolve.__init__(self)
        self._conf = None
        self._tsInfo = None
        self._rpmdb = None
        self._up = None
        self._comps = None
        self._history = None
        self._igroups = None
        self._pkgSack = None
        self._lockfile = None
        self._tags = None
        self._upinfo = None
        self._fssnap = None
        self._ts_save_file = None
        self.skipped_packages = []   # packages skip by the skip-broken code
        self._not_found_a = {}
        self._not_found_i = {}
        self.logger = logging.getLogger("yum.YumBase")
        self.verbose_logger = logging.getLogger("yum.verbose.YumBase")
        self.file_logger = logging.getLogger("yum.filelogging.YumBase")
        self._override_sigchecks = False
        self._repos = RepoStorage(self)
        self.repo_setopts = {} # since we have to use repo_setopts in base and 
                               # not in cli - set it up as empty so no one
                               # trips over it later

        # Start with plugins disabled
        self.disablePlugins()

        self.localPackages = [] # for local package handling

        self.mediagrabber = None
        self.arch = ArchStorage()
        self.preconf = _YumPreBaseConf()
        self.prerepoconf = _YumPreRepoConf()

        self.run_with_package_names = set()
        self._cleanup = []
        self.exit_code = 0

        self.updateinfo_filters = {}

    def __del__(self):
        try:
            self.close()
            self.closeRpmDB()
            self.doUnlock()
            # call cleanup callbacks
            for cb in self._cleanup: cb()
        except Errors.RepoError, e:
            self.verbose_logger.debug("Exception %s %s in %s ignored" % (repr(e), str(e), self.__del__))

    def close(self):
        """Close the history and repo objects."""

        # We don't want to create the object, so we test if it's been created
        if self._history is not None:
            self.history.close()

        if self._igroups is not None:
            self.igroups.close()

        if self._repos:
            self._repos.close()

    def _transactionDataFactory(self):
        """Factory method returning TransactionData object"""
        return transactioninfo.TransactionData()

    def doGenericSetup(self, cache=0):
        """Do a default setup for all the normal or necessary yum
        components.  This function is really just a shorthand for
        testing purposes.

        :param cache: whether to run in cache only mode, which will
           run only from the system cache
        """
        self.preconf.init_plugins = False
        self.conf.cache = cache

    def doConfigSetup(self, fn='/etc/yum/yum.conf', root='/', init_plugins=True,
            plugin_types=(plugins.TYPE_CORE,), optparser=None, debuglevel=None,
            errorlevel=None):
        """Deprecated.  Perform configuration setup.

        :param fn: the name of the configuration file to use
        :param root: the root directory to use
        :param init_plugins: whether to initialize plugins before
           running yum
        :param plugin_types: a tuple containing the types to plugins
           to load
        :param optparser: the option parser to use for configuration
        :param debuglevel: the minimum debug logging level to output
           messages from
        :param errorlevel: the minimum error logging level to output
           messages from
        """
        warnings.warn(_('doConfigSetup() will go away in a future version of Yum.\n'),
                Errors.YumFutureDeprecationWarning, stacklevel=2)

        if hasattr(self, 'preconf'):
            self.preconf.fn = fn
            self.preconf.root = root
            self.preconf.init_plugins = init_plugins
            self.preconf.plugin_types = plugin_types
            self.preconf.optparser = optparser
            self.preconf.debuglevel = debuglevel
            self.preconf.errorlevel = errorlevel

        return self.conf
        
    def _getConfig(self, **kwargs):
        '''
        Parse and load Yum's configuration files and call hooks initialise
        plugins and logging. Uses self.preconf for pre-configuration,
        configuration. '''

        # ' xemacs syntax hack

        if kwargs:
            warnings.warn('Use .preconf instead of passing args to _getConfig')

        if self._conf:
            return self._conf
        conf_st = time.time()            

        if kwargs:
            for arg in ('fn', 'root', 'init_plugins', 'plugin_types',
                        'optparser', 'debuglevel', 'errorlevel',
                        'disabled_plugins', 'enabled_plugins'):
                if arg in kwargs:
                    setattr(self.preconf, arg, kwargs[arg])

        fn = self.preconf.fn
        root = self.preconf.root
        init_plugins = self.preconf.init_plugins
        plugin_types = self.preconf.plugin_types
        optparser = self.preconf.optparser
        debuglevel = self.preconf.debuglevel
        errorlevel = self.preconf.errorlevel
        disabled_plugins = self.preconf.disabled_plugins
        enabled_plugins = self.preconf.enabled_plugins
        syslog_ident    = self.preconf.syslog_ident
        syslog_facility = self.preconf.syslog_facility
        syslog_device   = self.preconf.syslog_device
        releasever = self.preconf.releasever
        arch = self.preconf.arch
        uuid = self.preconf.uuid
        
        if arch: # if preconf is setting an arch we need to pass that up
            self.arch.setup_arch(arch)
        else:
            arch = self.arch.canonarch

        # TODO: Remove this block when we no longer support configs outside
        # of /etc/yum/
        if fn == '/etc/yum/yum.conf' and not os.path.exists(fn):
            # Try the old default
            fn = '/etc/yum.conf'

        startupconf = config.readStartupConfig(fn, root, releasever)
        startupconf.arch = arch
        startupconf.basearch = self.arch.basearch
        if uuid:
            startupconf.uuid = uuid
        
        if startupconf.gaftonmode:
            global _wrap_yum_i18n__
            _wrap_yum_i18n__ = yum.i18n.dummy_wrapper
            global _wrap_yum_i18n_P_
            _wrap_yum_i18n_P_ = yum.i18n.dummyP_wrapper

        if debuglevel != None:
            startupconf.debuglevel = debuglevel
        if errorlevel != None:
            startupconf.errorlevel = errorlevel
        if syslog_ident != None:
            startupconf.syslog_ident = syslog_ident
        if syslog_facility != None:
            startupconf.syslog_facility = syslog_facility
        if syslog_device != None:
            startupconf.syslog_device = syslog_device
        if releasever == '/':
            if startupconf.installroot == '/':
                releasever = None
            else:
                releasever = yum.config._getsysver("/",startupconf.distroverpkg)
        if releasever != None:
            startupconf.releasever = releasever

        self.doLoggingSetup(startupconf.debuglevel, startupconf.errorlevel,
                            startupconf.syslog_ident,
                            startupconf.syslog_facility,
                            startupconf.syslog_device)

        if init_plugins and startupconf.plugins:
            self.doPluginSetup(optparser, plugin_types, startupconf.pluginpath,
                    startupconf.pluginconfpath,disabled_plugins,enabled_plugins)

        self._conf = config.readMainConfig(startupconf)

        # update urlgrabber defaults
        mc = self._conf.max_connections
        if mc > 0:
            default_grabber.opts.max_connections = mc
        default_grabber.opts.timedhosts = self._conf.cachedir + '/timedhosts'

        #  We don't want people accessing/altering preconf after it becomes
        # worthless. So we delete it, and thus. it'll raise AttributeError
        del self.preconf

        # Packages used to run yum...
        for pkgname in self.conf.history_record_packages:
            self.run_with_package_names.add(pkgname)

        self._cashe = None
        if cashe is not None:
            self._cashe = cashe.CAShe(self.conf.cashe_root_dir)

        # run the postconfig plugin hook
        self.plugins.run('postconfig')
        #  Note that Pungi has historically replaced _getConfig(), and it sets
        # up self.conf.yumvar but not self.yumvar ... and AFAIK nothing needs
        # to use YumBase.yumvar, so it's probably easier to just semi-deprecate
        # this (core now only uses YumBase.conf.yumvar).
        self.yumvar = self.conf.yumvar

        # who are we:
        self.conf.uid = os.geteuid()
        # repos are ver/arch specific so add $basearch/$releasever
        self.conf._repos_persistdir = os.path.normpath('%s/repos/%s/%s/'
               % (self.conf.persistdir,  self.yumvar.get('basearch', '$basearch'),
                  self.yumvar.get('releasever', '$releasever')))        
        self.doFileLogSetup(self.conf.uid, self.conf.logfile)
        self.verbose_logger.debug('Config time: %0.3f' % (time.time() - conf_st))
        self.plugins.run('init')
        return self._conf
        

    def doLoggingSetup(self, debuglevel, errorlevel,
                       syslog_ident=None, syslog_facility=None,
                       syslog_device='/dev/log'):
        """Perform logging related setup.

        :param debuglevel: the minimum debug logging level to output
           messages from
        :param errorlevel: the minimum error logging level to output
           messages from
        :param syslog_ident: the ident of the syslog to use
        :param syslog_facility: the name of the syslog facility to use
        :param syslog_device: the syslog device to use
        """
        logginglevels.doLoggingSetup(debuglevel, errorlevel,
                                     syslog_ident, syslog_facility,
                                     syslog_device)

    def doFileLogSetup(self, uid, logfile):
        """Set up the logging file.

        :param uid: the user id of the current user
        :param logfile: the name of the file to use for logging
        """
        logginglevels.setFileLog(uid, logfile, self._cleanup)

    def getReposFromConfigFile(self, repofn, repo_age=None, validate=None):
        """Read in repositories from a config .repo file.

        :param repofn: a string specifying the path of the .repo file
           to read
        :param repo_age: the last time that the .repo file was
           modified, in seconds since the epoch
        """
        if repo_age is None:
            repo_age = os.stat(repofn)[8]
        
        confpp_obj = ConfigPreProcessor(repofn, vars=self.conf.yumvar)
        parser = ConfigParser()
        try:
            parser.readfp(confpp_obj)
        except ParsingError, e:
            raise Errors.ConfigError(exception2msg(e))

        # Check sections in the .repo file that was just slurped up
        for section in parser.sections():

            if section in ['main', 'installed']:
                continue

            # Check the repo.id against the valid chars
            bad = misc.validate_repoid(section)

            if bad:
                self.logger.warning("Bad id for repo: %s, byte = %s %d" %
                                    (section, bad, section.find(bad)))
                continue

            try:
                thisrepo = self.readRepoConfig(parser, section)
            except (Errors.RepoError, Errors.ConfigError), e:
                self.logger.warning(e)
                continue
            else:
                thisrepo.repo_config_age = repo_age
                thisrepo.repofile = repofn

                thisrepo.base_persistdir = self.conf._repos_persistdir

            # do the wildcard ones first
            # The keys are in indeterminate order at this point, *sigh*.
            for i in sorted(self.repo_setopts):
                #  Skip normal names, as we want to do wildcard matches first
                # and then override with specific id stuff.
                if not misc.re_glob(i):
                    continue

                if fnmatch.fnmatch(thisrepo.id, i):
                    for opt in self.repo_setopts[i].items:
                        self._checkOption(opt, thisrepo)
                        setattr(thisrepo, opt, getattr(self.repo_setopts[i], opt))
                
            if thisrepo.id in self.repo_setopts:
                for opt in self.repo_setopts[thisrepo.id].items:
                    self._checkOption(opt, thisrepo)
                    setattr(thisrepo, opt, getattr(self.repo_setopts[thisrepo.id], opt))
                    
            if validate and not validate(thisrepo):
                continue
                    
            if thisrepo.ssl_check_cert_permissions:
                for fn in  (thisrepo.sslcacert,
                            thisrepo.sslclientcert, thisrepo.sslclientkey):
                    if not fn:
                        continue
                    #  If we can't read the SSL certs. we need to skip the repo.
                    # if we don't have all the data.
                    if not os.access(fn, os.R_OK):
                        msg="Repo %s forced skip_if_unavailable=True due to: %s"
                        if thisrepo.enabled:
                            # Don't spam messages for disabled repos.
                            self.logger.warning(msg % (thisrepo.id, fn))
                        thisrepo.skip_if_unavailable = True

            # Got our list of repo objects, add them to the repos
            # collection
            try:
                self._repos.add(thisrepo)
            except Errors.RepoError, e:
                self.logger.warning(e)
        
    def _checkOption(self, opt, thisrepo):
        """Quit if the option is invalid"""
        if not hasattr(thisrepo, opt):
            msg = "Invalid option: %s"
            raise Errors.ConfigError(msg % opt)

    def getReposFromConfig(self):
        """Read in repositories from the main yum conf file, and from
        .repo files.  The location of the main yum conf file is given
        by self.conf.config_file_path, and the location of the
        directory of .repo files is given by self.conf.reposdir.
        """
        # Read .repo files from directories specified by the reposdir option
        # (typically /etc/yum/repos.d)
        repo_config_age = self.conf.config_file_age
        
        # Get the repos from the main yum.conf file
        self.getReposFromConfigFile(self.conf.config_file_path, repo_config_age)

        for reposdir in self.conf.reposdir:
            # this check makes sure that our dirs exist properly.
            # if they aren't in the installroot then don't prepend the installroot path
            # if we don't do this then anaconda likes to not  work.
            if os.path.exists(self.conf.installroot+'/'+reposdir):
                reposdir = self.conf.installroot + '/' + reposdir
            reposdir = os.path.normpath(reposdir)

            if os.path.isdir(reposdir):
                for repofn in sorted(glob.glob('%s/*.repo' % reposdir)):
                    if not os.access(repofn, os.R_OK):
                        self.logger.warning(_("Skipping unreadable repository %s"), repr(repofn))
                        continue
                    thisrepo_age = os.stat(repofn)[8]
                    if thisrepo_age < repo_config_age:
                        thisrepo_age = repo_config_age
                    self.getReposFromConfigFile(repofn, repo_age=thisrepo_age)

    def readRepoConfig(self, parser, section):
        """Parse an INI file section for a repository.

        :param parser: :class:`ConfigParser` or similar object to read
           INI file values from
        :param section: INI file section to read
        :return: :class:`yum.yumRepo.YumRepository` instance
        """
        repo = yumRepo.YumRepository(section)
        try:
            repo.populate(parser, section, self.conf)
        except ValueError, e:
            msg = _('Repository %r: Error parsing config: %s' % (section,e))
            raise Errors.ConfigError, msg

        # Ensure that the repo name is set
        if not repo.name:
            repo.name = section
            self.logger.error(_('Repository %r is missing name in configuration, '
                    'using id') % section)
        repo.name = to_unicode(repo.name)

        # Set attributes not from the config file
        repo.old_base_cache_dir = getattr(self, '_old_cachedir', '')
        repo.basecachedir = self.conf.cachedir
        repo.yumvar.update(self.conf.yumvar)
        repo._cashe = self._cashe
        repo.cfg = parser
        # Enable parallel downloading
        repo._async = repo.async
        # Allow caching local repos
        if repo.keepcache and any(u.startswith('file:') for u in repo.baseurl):
            repo.copy_local = True
        return repo

    def disablePlugins(self):
        """Disable yum plugins."""

        self.plugins = plugins.DummyYumPlugins()
    
    def doPluginSetup(self, optparser=None, plugin_types=None, searchpath=None,
            confpath=None,disabled_plugins=None,enabled_plugins=None):
        """Initialise and enable yum plugins.
        Note: _getConfig() will also initialise plugins if instructed
        to. Only call this method directly if not calling _getConfig()
        or calling doConfigSetup(init_plugins=False).

        :param optparser: the :class:`OptionParser` instance to use
           for this run
        :param plugin_types: a sequence specifying the types of plugins to load.
           This should be a sequence containing one or more of the
           yum.plugins.TYPE_...  constants. If None (the default), all plugins
           will be loaded
        :param searchpath: a list of directories to look in for plugins. A
           default will be used if no value is specified
        :param confpath: a list of directories to look in for plugin
           configuration files. A default will be used if no value is
           specified
        :param disabled_plugins: a list of plugins to be disabled    
        :param enabled_plugins: a list plugins to be enabled
        """
        if isinstance(self.plugins, plugins.YumPlugins):
            raise RuntimeError(_("plugins already initialised"))

        self.plugins = plugins.YumPlugins(self, searchpath, optparser,
                plugin_types, confpath, disabled_plugins, enabled_plugins)

    
    def doRpmDBSetup(self):
        """Deprecated.  Set up the rpm database."""

        warnings.warn(_('doRpmDBSetup() will go away in a future version of Yum.\n'),
                Errors.YumFutureDeprecationWarning, stacklevel=2)

        return self._getRpmDB()
    
    def _getRpmDB(self):
        """sets up a holder object for important information from the rpmdb"""

        if self._rpmdb is None:
            rpmdb_st = time.time()
            self.verbose_logger.log(logginglevels.DEBUG_4,
                                    _('Reading Local RPMDB'))
            self._rpmdb = rpmsack.RPMDBPackageSack(root=self.conf.installroot,
                                                   releasever=self.conf.yumvar['releasever'],
                                                   persistdir=self.conf.persistdir)
            self.verbose_logger.debug('rpmdb time: %0.3f' % (time.time() - rpmdb_st))
        return self._rpmdb

    def closeRpmDB(self):
        """Closes down the instances of rpmdb that could be open."""

        if self._rpmdb is not None:
            self._rpmdb.ts = None
            self._rpmdb.dropCachedData()
        self._rpmdb = None
        self._ts = None
        self._tsInfo = None
        self._up = None
        self.comps = None
        self.igroups = None
    
    def _deleteTs(self):
        del self._ts
        self._ts = None

    def doRepoSetup(self, thisrepo=None):
        """Deprecated. Set up the yum repositories.

        :param thisrepo: the repository to set up.  If None, all
           repositories will be set up
        :return: the set up repos
        """
        warnings.warn(_('doRepoSetup() will go away in a future version of Yum.\n'),
                Errors.YumFutureDeprecationWarning, stacklevel=2)

        return self._getRepos(thisrepo, True)

    def _getRepos(self, thisrepo=None, doSetup = False):
        """ For each enabled repository set up the basics of the repository. """
        if hasattr(self, 'prerepoconf'):
            self.conf # touch the config class first

            if (self.conf.installroot != '/' and
                not hasattr(self, '_old_cachedir')):
                # Try loading cache from outside...
                ir = len(self.conf.installroot)
                self._old_cachedir = self.conf.cachedir[ir:]

            self.getReposFromConfig()

        #  For rhnplugin, and in theory other stuff, calling
        # .getReposFromConfig() recurses back into this function but only once.
        # This means that we have two points on the stack leaving the above call
        # but only one of them can do the repos setup. BZ 678043.
        if hasattr(self, 'prerepoconf'):
            # Recursion
            prerepoconf = self.prerepoconf
            del self.prerepoconf

            self.repos.setProgressBar(prerepoconf.progressbar,
                                      prerepoconf.multi_progressbar)
            self.repos.callback = prerepoconf.callback
            self.repos.setFailureCallback(prerepoconf.failure_callback)
            self.repos.setInterruptCallback(prerepoconf.interrupt_callback)
            self.repos.confirm_func = prerepoconf.confirm_func
            self.repos.gpg_import_func = prerepoconf.gpg_import_func
            self.repos.gpgca_import_func = prerepoconf.gpgca_import_func
            if prerepoconf.cachedir is not None:
                self.repos.setCacheDir(prerepoconf.cachedir)
            if prerepoconf.cache is not None:
                self.repos.setCache(prerepoconf.cache)


        if doSetup:
            repo_st = time.time()        
            self._repos.doSetup(thisrepo)
            self.verbose_logger.debug('repo time: %0.3f' % (time.time() - repo_st))        
        return self._repos

    def _delRepos(self):
        del self._repos
        self._repos = RepoStorage(self)
    
    def doSackSetup(self, archlist=None, thisrepo=None):
        """Deprecated. Populate the package sacks with information
        from our repositories.

        :param archlist: a list of the names of archs to include.  If
           None, all arches are set up
        :param thisrepo: the repository to use.  If None, all enabled
           repositories are used
        """
        warnings.warn(_('doSackSetup() will go away in a future version of Yum.\n'),
                Errors.YumFutureDeprecationWarning, stacklevel=2)

        return self._getSacks(archlist=archlist, thisrepo=thisrepo)
        
    def _getSacks(self, archlist=None, thisrepo=None):
        """populates the package sacks for information from our repositories,
           takes optional archlist for archs to include"""

        # FIXME: Fist of death ... normally we'd do either:
        #
        # 1. use self._pkgSack is not None, and only init. once.
        # 2. auto. correctly re-init each time a repo is added/removed
        #
        # ...we should probably just smeg it and do #2, but it's hard and will
        # probably break something (but it'll "fix" excludes).
        #  #1 can't be done atm. because we did self._pkgSack and external
        # tools now rely on being able to create an empty sack and then have it
        # auto. re-init when they add some stuff. So we add a bit more "clever"
        # and don't setup the pkgSack to not be None when it's empty. This means
        # we skip excludes/includes/etc. ... but there's no packages, so
        # hopefully that's ok.
        if self._pkgSack is not None and thisrepo is None:
            return self._pkgSack
        
        if thisrepo is None:
            repos = 'enabled'
        else:
            repos = self.repos.findRepos(thisrepo)
        
        self.verbose_logger.debug(_('Setting up Package Sacks'))
        sack_st = time.time()
        if not archlist:
            archlist = self.arch.archlist
        
        archdict = {}
        for arch in archlist:
            archdict[arch] = 1
        
        self.repos.getPackageSack().setCompatArchs(archdict)
        self.repos.populateSack(which=repos)
        if not self.repos.getPackageSack():
            return self.repos.getPackageSack() # ha ha, see above
        self._pkgSack = self.repos.getPackageSack()
        
        self.excludePackages()
        self._pkgSack.excludeArchs(archlist)
        
        #FIXME - this could be faster, too.
        if repos == 'enabled':
            repos = self.repos.listEnabled()
        for repo in repos:
            self.includePackages(repo)
            self.excludePackages(repo)
        self.plugins.run('exclude')
        self._pkgSack.buildIndexes()

        # now go through and kill pkgs based on pkg.repo.cost()
        self.costExcludePackages()
        self.verbose_logger.debug('pkgsack time: %0.3f' % (time.time() - sack_st))
        return self._pkgSack
    
    
    def _delSacks(self):
        """reset the package sacks back to zero - making sure to nuke the ones
           in the repo objects, too - where it matters"""
           
        # nuke the top layer
        
        self._pkgSack = None
           
        for repo in self.repos.repos.values():
            if hasattr(repo, '_resetSack'):
                repo._resetSack()
            else:
                warnings.warn(_('repo object for repo %s lacks a _resetSack method\n') +
                        _('therefore this repo cannot be reset.\n'),
                        Errors.YumFutureDeprecationWarning, stacklevel=2)
            
           
    def doUpdateSetup(self):
        """Deprecated. Set up the update object in the base class and populate the
        updates, obsoletes, and other lists.
        """
        warnings.warn(_('doUpdateSetup() will go away in a future version of Yum.\n'),
                Errors.YumFutureDeprecationWarning, stacklevel=2)

        return self._getUpdates()
        
    def _getUpdates(self):
        """setups up the update object in the base class and fills out the
           updates, obsoletes and others lists"""
        
        if self._up:
            return self._up
        
        self.verbose_logger.debug(_('Building updates object'))

        up_st = time.time()

        self._up = rpmUtils.updates.Updates(self.rpmdb.simplePkgList(), self.pkgSack.simplePkgList())
        if self.conf.debuglevel >= 7:
            self._up.debug = 1
        
        if hasattr(self, '_up_obs_hack'):
            self._up.rawobsoletes = self._up_obs_hack.rawobsoletes
            del self._up_obs_hack
        elif self.conf.obsoletes:
            obs_init = time.time()    
            #  Note: newest=True here is semi-required for repos. with multiple
            # versions. The problem is that if pkgA-2 _accidentally_ obsoletes
            # pkgB-1, and we keep all versions, we want to release a pkgA-3
            # that doesn't do the obsoletes ... and thus. not obsolete pkgB-1.
            self._up.rawobsoletes = self.pkgSack.returnObsoletes(newest=True)
            self.verbose_logger.debug('up:Obs Init time: %0.3f' % (time.time() - obs_init))

        self._up.myarch = self.arch.canonarch
        self._up._is_multilib = self.arch.multilib
        self._up._archlist = self.arch.archlist
        self._up._multilib_compat_arches = self.arch.compatarches
        self._up.exactarch = self.conf.exactarch
        self._up.exactarchlist = self.conf.exactarchlist
        up_pr_st = time.time()
        self._up.doUpdates()
        self.verbose_logger.debug('up:simple updates time: %0.3f' % (time.time() - up_pr_st))

        if self.conf.obsoletes:
            obs_st = time.time()
            self._up.doObsoletes()
            self.verbose_logger.debug('up:obs time: %0.3f' % (time.time() - obs_st))

        cond_up_st = time.time()                    
        self._up.condenseUpdates()
        self.verbose_logger.debug('up:condense time: %0.3f' % (time.time() - cond_up_st))
        self.verbose_logger.debug('updates time: %0.3f' % (time.time() - up_st))        
        return self._up
    
    def doGroupSetup(self):
        """Deprecated. Create and populate the groups object."""

        warnings.warn(_('doGroupSetup() will go away in a future version of Yum.\n'),
                Errors.YumFutureDeprecationWarning, stacklevel=2)

        self.comps = None
        return self._getGroups()

    def _setGroups(self, val):
        if val is None:
            # if we unset the comps object, we need to undo which repos have
            # been added to the group file as well
            if self._repos:
                #  Used to do listGroupsEnabled(), which seems fine but requires
                # calling .listEnalbed() ... which doesn't work on __del__ path
                # if we haven't already called that (due to
                # "prelistenabledrepos" plugins). So just blank it for
                # all repos.
                for repo in self._repos.sort():
                    repo.groups_added = False
        self._comps = val
    
    def _getGroups(self):
        """create the groups object that will store the comps metadata
           finds the repos with groups, gets their comps data and merge it
           into the group object"""
        
        if self._comps:
            return self._comps

        group_st = time.time()            
        self.verbose_logger.log(logginglevels.DEBUG_4,
                                _('Getting group metadata'))
        reposWithGroups = []
        #  Need to make sure the groups data is ready to read. Really we'd want
        # to add groups to the mdpolicy list of the repo. but we don't atm.
        self.pkgSack
        for repo in self.repos.listGroupsEnabled():
            if repo.groups_added: # already added the groups from this repo
                reposWithGroups.append(repo)
                continue
                
            if not repo.ready():
                raise Errors.RepoError("Repository '%s' not yet setup" % repo,
                                       repo=repo)
            try:
                groupremote = repo.getGroupLocation()
            except Errors.RepoMDError, e:
                pass
            else:
                reposWithGroups.append(repo)
        
        # now we know which repos actually have groups files.
        overwrite = self.conf.overwrite_groups
        self._comps = comps.Comps(overwrite_groups = overwrite)

        if self.conf.group_command == 'objects':
            #  Add the ids for installed groups/envs as though sys is a repo.
            # makes everything easier (comps.return_groups() etc. works)...
            self._comps.compscount += 1
            for gid in self.igroups.groups:
                grp = comps.Group()
                grp.name = grp.groupid = gid
                grp._weak = True
                self._comps.add_group(grp)
            for evgid in self.igroups.environments:
                env = comps.Environment()
                env.name = env.environmentid = evgid
                env._weak = True
                self._comps.add_environment(env)

        for repo in reposWithGroups:
            if repo.groups_added: # already added the groups from this repo
                continue
                
            self.verbose_logger.log(logginglevels.DEBUG_4,
                _('Adding group file from repository: %s'), repo)
            groupfile = repo.getGroups()
            if not groupfile:
                msg = _('Failed to retrieve group file for repository: %s') % repo
                self.logger.critical(msg)
                continue
            try:
                self._comps.add(groupfile)
            except (Errors.GroupsError,Errors.CompsException), e:
                msg = _('Failed to add groups file for repository: %s - %s') % (repo, exception2msg(e))
                self.logger.critical(msg)
            else:
                repo.groups_added = True

        if self._comps.compscount == 0:
            raise Errors.GroupsError, _('No Groups Available in any repository')

        #  Note that this means that grp.installed is not usable, when using
        # groups as objects ... but that's GOOD.
        if self.conf.group_command != 'objects':
            self._comps.compile(self.rpmdb.simplePkgList())
        self.verbose_logger.debug('group time: %0.3f' % (time.time() - group_st))                
        return self._comps

    def _getTags(self):
        """ create the tags object used to search/report from the pkgtags 
            metadata"""
        
        tag_st = time.time()
        self.verbose_logger.log(logginglevels.DEBUG_4,
                                _('Getting pkgtags metadata'))
        
        if self._tags is None:
            self._tags = yum.pkgtag_db.PackageTags()
           
            for repo in self.repos.listEnabled():
                if 'pkgtags' not in repo.repoXML.fileTypes():
                    continue

                self.verbose_logger.log(logginglevels.DEBUG_4,
                    _('Adding tags from repository: %s'), repo)
                
                # fetch the sqlite tagdb
                try:
                    tag_md = repo.retrieveMD('pkgtags')
                    tag_sqlite  = misc.repo_gen_decompress(tag_md,
                                                           'pkgtags.sqlite',
                                                           cached=repo.cache)
                    # feed it into _tags.add()
                    self._tags.add(repo.id, tag_sqlite)
                except (Errors.RepoError, Errors.PkgTagsError), e:
                    msg = _('Failed to add Pkg Tags for repository: %s - %s') % (repo, exception2msg(e))
                    self.logger.critical(msg)
                    
                
        self.verbose_logger.debug('tags time: %0.3f' % (time.time() - tag_st))
        return self._tags
        

    def _getUpdateinfo(self):
        """ create the Update Info object used to search/report the updateinfo
            metadata"""

        upi_st = time.time()
        self.verbose_logger.log(logginglevels.DEBUG_4,
                                _('Getting updateinfo metadata'))

        if self._upinfo is None:
            logger = logging.getLogger("yum.update_md")
            vlogger = logging.getLogger("yum.verbose.update_md")
            self._upinfo = update_md.UpdateMetadata(logger=logger,
                                                    vlogger=vlogger)

            self.pkgSack  # Preload the sack now, to honor skip_if_unavailable
            for repo in self.repos.listEnabled():
                if 'updateinfo' not in repo.repoXML.fileTypes():
                    continue

                self.verbose_logger.log(logginglevels.DEBUG_4,
                    _('Adding Update Info from repository: %s'), repo)

                try:
                    self._upinfo.add(repo)
                except Errors.RepoMDError, e:
                    msg = _('Failed to add Update Info for repository: %s - %s') % (repo, exception2msg(e))
                    self.logger.critical(msg)

        self.verbose_logger.debug('updateinfo time: %0.3f' %
                                  (time.time() - upi_st))
        return self._upinfo

    def _getHistory(self):
        """auto create the history object that to access/append the transaction
           history information. """
        if self._history is None:
            pdb_path = self.conf.persistdir + "/history"
            self._history = yum.history.YumHistory(root=self.conf.installroot,
                                                   db_path=pdb_path,
                                                   releasever=self.conf.yumvar['releasever'])
        return self._history
    
    def _getFSsnap(self):
        """ create the fssnap object used to query/create snapshots. """
        if self._fssnap is None:
            devices = self.conf.fssnap_devices
            self._fssnap = yum.fssnapshots._FSSnap(root=self.conf.installroot,
                                                   devices=devices,
                                                   logger=self.file_logger)

        return self._fssnap

    def _getIGroups(self):
        """auto create the installed groups object that to access/change the
           installed groups information. """
        if self._igroups is None:
            pdb_path = self.conf.persistdir + "/groups"
            self._igroups = yum.igroups.InstalledGroups(db_path=pdb_path)
        return self._igroups

    # properties so they auto-create themselves with defaults
    repos = property(fget=lambda self: self._getRepos(),
                     fset=lambda self, value: setattr(self, "_repos", value),
                     fdel=lambda self: self._delRepos(),
                     doc="Repo Storage object - object of yum repositories")
    pkgSack = property(fget=lambda self: self._getSacks(),
                       fset=lambda self, value: setattr(self, "_pkgSack", value),
                       fdel=lambda self: self._delSacks(),
                       doc="Package sack object - object of yum package objects")
    conf = property(fget=lambda self: self._getConfig(),
                    fset=lambda self, value: setattr(self, "_conf", value),
                    fdel=lambda self: setattr(self, "_conf", None),
                    doc="Yum Config Object")
    rpmdb = property(fget=lambda self: self._getRpmDB(),
                     fset=lambda self, value: setattr(self, "_rpmdb", value),
                     fdel=lambda self: setattr(self, "_rpmdb", None),
                     doc="RpmSack object")
    tsInfo = property(fget=lambda self: self._getTsInfo(), 
                      fset=lambda self,value: self._setTsInfo(value), 
                      fdel=lambda self: self._delTsInfo(),
                      doc="Transaction Set information object")
    ts = property(fget=lambda self: self._getActionTs(), 
                  fdel=lambda self: self._deleteTs(),
                  doc="TransactionSet object")
    up = property(fget=lambda self: self._getUpdates(),
                  fset=lambda self, value: setattr(self, "_up", value),
                  fdel=lambda self: setattr(self, "_up", None),
                  doc="Updates Object")
    comps = property(fget=lambda self: self._getGroups(),
                     fset=lambda self, value: self._setGroups(value),
                     fdel=lambda self: setattr(self, "_comps", None),
                     doc="Yum Component/groups object")
    history = property(fget=lambda self: self._getHistory(),
                       fset=lambda self, value: setattr(self, "_history",value),
                       fdel=lambda self: setattr(self, "_history", None),
                       doc="Yum History Object")

    igroups = property(fget=lambda self: self._getIGroups(),
                       fset=lambda self, value: setattr(self, "_igroups",value),
                       fdel=lambda self: setattr(self, "_igroups", None),
                       doc="Yum Installed Groups Object")

    pkgtags = property(fget=lambda self: self._getTags(),
                       fset=lambda self, value: setattr(self, "_tags",value),
                       fdel=lambda self: setattr(self, "_tags", None),
                       doc="Yum Package Tags Object")

    upinfo = property(fget=lambda self: self._getUpdateinfo(),
                      fset=lambda self, value: setattr(self, "_upinfo", value),
                      fdel=lambda self: setattr(self, "_upinfo", None),
                      doc="Yum Update Info Object")

    fssnap = property(fget=lambda self: self._getFSsnap(),
                      fset=lambda self, value: setattr(self, "_fssnap",value),
                      fdel=lambda self: setattr(self, "_fssnap", None),
                      doc="Yum FS snapshot Object")
    
    def doSackFilelistPopulate(self):
        """Convenience function to populate the repositories with the
        filelist metadata, and emit a log message only if new
        information is actually populated.
        """
        necessary = False
        
        # I can't think of a nice way of doing this, we have to have the sack here
        # first or the below does nothing so...
        if self.pkgSack:
            for repo in self.repos.listEnabled():
                if repo in repo.sack.added:
                    if 'filelists' in repo.sack.added[repo]:
                        continue
                    else:
                        necessary = True
                else:
                    necessary = True

        if necessary:
            msg = _('Importing additional filelist information')
            self.verbose_logger.log(logginglevels.INFO_2, msg)
            self.repos.populateSack(mdtype='filelists')
           
    def yumUtilsMsg(self, func, prog):
        """Output a message that the given tool requires the yum-utils
        package, if it not installed.

        :param func: the function to output the message
        :param prog: the name of the tool that requires yum-utils
        """
        if self.rpmdb.contains(name="yum-utils"):
            return

        func(_("The program %s is found in the yum-utils package.") % self._try_bold(prog))

    def buildTransaction(self, unfinished_transactions_check=True):
        """Go through the packages in the transaction set, find them
        in the packageSack or rpmdb, and pack up the transaction set
        accordingly.

        :param unfinished_transactions_check: whether to check for
           unfinished transactions before building the new transaction
        """
        # FIXME: This is horrible, see below and yummain. Maybe create a real
        #        rescode object? :(
        self._depsolving_failed = False

        if (unfinished_transactions_check and
            misc.find_unfinished_transactions(yumlibpath=self.conf.persistdir)):
            msg = _('There are unfinished transactions remaining. You might ' \
                    'consider running yum-complete-transaction, or' \
                    ' "yum-complete-transaction --cleanup-only" and' \
                    ' "yum history redo last", first to finish them. If those' \
                    ' don\'t work you\'ll have to try removing/installing' \
                    ' packages by hand (maybe package-cleanup can help).')
            self.logger.critical(msg)
            self.yumUtilsMsg(self.logger.critical, "yum-complete-transaction")
            time.sleep(3)
        
        # XXX - we could add a conditional here to avoid running the plugins and 
        # limit_installonly_pkgs, etc - if we're being run from yum-complete-transaction
        # and don't want it to happen. - skv
        
        self.plugins.run('preresolve')
        ds_st = time.time()

        (rescode, restring) = self.resolveDeps()
        self._limit_installonly_pkgs()
        # if enabled clean up requirments when removing the things which brought them in.
        if self.conf.clean_requirements_on_remove:
            self.verbose_logger.log(logginglevels.INFO_2, _('--> Finding unneeded leftover dependencies'))
            self._remove_old_deps()
        #  We _must_ get rid of all the used tses before we go on, so that C-c
        # works for downloads / mirror failover etc.
        kern_pkgtup = None
        if rescode == 2 and self.conf.protected_packages:
            kern_pkgtup =misc.get_running_kernel_pkgtup(self.rpmdb.readOnlyTS())
        self.rpmdb.ts = None

        # do the skip broken magic, if enabled and problems exist
        (rescode, restring) = self._doSkipBroken(rescode, restring)

        self.plugins.run('postresolve', rescode=rescode, restring=restring)

        if self.tsInfo.changed:
            (rescode, restring) = self.resolveDeps(rescode == 1)
            # If transaction was changed by postresolve plugins then we should run skipbroken again
            (rescode, restring) = self._doSkipBroken(rescode, restring, clear_skipped=False )

        if self.tsInfo._pkgSack is not None: # Transactions have pkgSack?
            self.tsInfo.pkgSack.dropCachedData()

        # FIXME: This is horrible, see below and yummain. Maybe create a real
        #        rescode object? :(
        self._depsolving_failed = rescode == 1

        txmbrs = []
        if rescode == 2 and self.conf.protected_multilib and self.arch.multilib:
            txmbrs = self.tsInfo.getMembersWithState(None, TS_INSTALL_STATES)
        vers = {}
        for txmbr in txmbrs:
            if self.allowedMultipleInstalls(txmbr.po):
                continue # Just allow these, it's easier.

            #  In theory we could skip noarch packages here, but it's really
            # fast and there are some edge cases where it'll help.
            if txmbr.name not in vers:
                vers[txmbr.name] = [txmbr.po]
                continue
            vers[txmbr.name].append(txmbr.po)

        fine = []
        xrestring = []
        for pkgname in vers:
            if len(vers[pkgname]) <= 1:
                # We have to go govelling through the rpmdb data to get
                for pkg in self.rpmdb.searchNames([pkgname]):
                    if self.tsInfo.getMembersWithState(pkg.pkgtup,
                                                       TS_REMOVE_STATES):
                        continue
                    vers[pkgname].append(pkg)

            #  If we have multiple packages, they should be of different arches
            # and so if all the versions are equal, we should be fine.
            first = vers[pkgname][0]
            for other in vers[pkgname][1:]:
                if first.verEQ(other):
                    continue
                msg = _('Protected multilib versions: %s != %s')
                if not xrestring:
                    #  People are confused about protected mutilib ... so give
                    # them a nicer message.
                    bigmsg = _("""\
 Multilib version problems found. This often means that the root
cause is something else and multilib version checking is just
pointing out that there is a problem. Eg.:

  1. You have an upgrade for %(name)s which is missing some
     dependency that another package requires. Yum is trying to
     solve this by installing an older version of %(name)s of the
     different architecture. If you exclude the bad architecture
     yum will tell you what the root cause is (which package
     requires what). You can try redoing the upgrade with
     --exclude %(name)s.otherarch ... this should give you an error
     message showing the root cause of the problem.

  2. You have multiple architectures of %(name)s installed, but
     yum can only see an upgrade for one of those architectures.
     If you don't want/need both architectures anymore then you
     can remove the one with the missing update and everything
     will work.

  3. You have duplicate versions of %(name)s installed already.
     You can use "yum check" to get yum show these errors.

...you can also use --setopt=protected_multilib=false to remove
this checking, however this is almost never the correct thing to
do as something else is very likely to go wrong (often causing
much more problems).

""") % {'name' : pkgname}
                    msg = bigmsg + msg
                xrestring.append(msg % (first, other))
        if xrestring:
            rescode = 1
            self._depsolving_failed = False
            restring = xrestring

        #  This is a version of the old "protect-packages" plugin, it allows
        # you to erase duplicates and do remove+install.
        #  But we don't allow you to turn it off!:)
        protect_states = [TS_OBSOLETED, TS_ERASE]
        txmbrs = []
        if rescode == 2 and self.conf.protected_packages:
            protected = set(self.conf.protected_packages)
            txmbrs = self.tsInfo.getMembersWithState(None, protect_states)
        bad_togo = {}
        for txmbr in txmbrs:
            if kern_pkgtup is not None and txmbr.pkgtup == kern_pkgtup:
                pass
            elif kern_pkgtup is not None and txmbr.name == kern_pkgtup[0]:
                #  We don't care if they've explicitly set protected on the
                # kernel package. Because we don't allow you to uninstall the
                # running one so it has _special_ semantics anyway.
                continue
            elif txmbr.name not in protected:
                continue
            if txmbr.name not in bad_togo:
                bad_togo[txmbr.name] = []
            bad_togo[txmbr.name].append(txmbr.pkgtup)
        for ipkg in self.rpmdb.searchNames(bad_togo.keys()):
            if (kern_pkgtup is not None and ipkg.name == kern_pkgtup[0] and
                kern_pkgtup in bad_togo[kern_pkgtup[0]]):
                continue # If "running kernel" matches, it's always bad.
            if ipkg.name not in bad_togo:
                continue
            # If there is at least one version not being removed, allow it
            if ipkg.pkgtup not in bad_togo[ipkg.name]:
                del bad_togo[ipkg.name]
        for pkgname in bad_togo.keys():
            if (kern_pkgtup is not None and pkgname == kern_pkgtup[0] and
                kern_pkgtup in bad_togo[kern_pkgtup[0]]):
                continue # If "running kernel" matches, it's always bad.
            for txmbr in self.tsInfo.matchNaevr(name=pkgname):
                if txmbr.name not in bad_togo:
                    continue
                if txmbr.pkgtup in bad_togo[ipkg.name]:
                    continue
                # If we are installing one version we aren't removing, allow it
                if txmbr.output_state in TS_INSTALL_STATES:
                    del bad_togo[ipkg.name]

        if bad_togo:
            rescode = 1
            restring = []
            for pkgname in sorted(bad_togo):
                restring.append(_('Trying to remove "%s", which is protected') %
                                pkgname)
            self._depsolving_failed = False

        if rescode == 2:
            if self.conf.autosavets:
                self.save_ts(auto=True)

        # Make sure we don't fail in rpm if we're installing a package that is
        # allowed multiple installs but has a newer version already installed.
        # Note that we already have a similar check in install(), but here we
        # do it to cover anything that was pulled in as a dependency.
        if rpm.RPMPROB_FILTER_OLDPACKAGE not in self.tsInfo.probFilterFlags:
            for m in self.tsInfo.getMembers():
                if m.ts_state == 'i' and self.allowedMultipleInstalls(m.po):
                    if self._enable_oldpackage_flag(m.po):
                        break

        self.verbose_logger.debug('Depsolve time: %0.3f' % (time.time() - ds_st))
        return rescode, restring

    def _doSkipBroken(self,rescode, restring, clear_skipped=True):
        ''' do skip broken if it is enabled '''
        # if depsolve failed and skipbroken is enabled
        # The remove the broken packages from the transactions and
        # Try another depsolve
        if self.conf.skip_broken and rescode==1:
            if clear_skipped:
                self.skipped_packages = []    # reset the public list of skipped packages.
            sb_st = time.time()
            rescode, restring = self._skipPackagesWithProblems(rescode, restring)
            self._printTransaction()        
            self.verbose_logger.debug('Skip-Broken time: %0.3f' % (time.time() - sb_st))
        return (rescode, restring)
            

    def _skipPackagesWithProblems(self, rescode, restring):
        ''' Remove the packages with depsolve errors and depsolve again '''

        def _remove(po, depTree, toRemove):
            if not po:
                return
            self._getPackagesToRemove(po, depTree, toRemove)
            # Only remove non installed packages from pkgSack
            _remove_from_sack(po)

        def _remove_from_sack(po):
            # get all compatible arch packages from pkgSack
            # we need to remove them too so i386 packages are not 
            # dragged in when a x86_64 is skipped.
            pkgs = self._getPackagesToRemoveAllArch(po)
            for pkg in pkgs:
                if not po.repoid == 'installed' and pkg not in removed_from_sack:             
                    self.verbose_logger.debug('SKIPBROKEN: removing %s from pkgSack & updates' % str(po))
                    self.pkgSack.delPackage(pkg)
                    self.up.delPackage(pkg.pkgtup)
                    removed_from_sack.add(pkg)

        # Keep removing packages & Depsolve until all errors is gone
        # or the transaction is empty
        count = 0
        skipped_po = set()
        removed_from_sack = set()
        orig_restring = restring    # Keep the old error messages 
        looping = 0 
        while (len(self.po_with_problems) > 0 and rescode == 1):
            count += 1
            #  Remove all the rpmdb cache data, this is somewhat heavy handed
            # but easier than removing/altering specific bits of the cache ...
            # and skip-broken shouldn't care too much about speed.
            self.rpmdb.transactionReset()
            self.installedFileRequires = None # Kind of hacky
            self.verbose_logger.debug("SKIPBROKEN: ########### Round %i ################" , count)
            if count == 30: # Failsafe, to avoid endless looping
                self.verbose_logger.debug('SKIPBROKEN: Too many loops ')
                break
            self._printTransaction()        
            depTree = self._buildDepTree()
            startTs = set(self.tsInfo)
            toRemove = set()
            for po,wpo,err in self.po_with_problems:
                # check if the problem is caused by a package in the transaction
                if not self.tsInfo.exists(po.pkgtup):
                    _remove(wpo, depTree, toRemove)
                else:
                    _remove(po,  depTree, toRemove)
            for po in toRemove:
                skipped = self._skipFromTransaction(po)
                for skip in skipped:
                    skipped_po.add(skip)
                    # make sure we get the compat arch packages skip from pkgSack and up too.
                    if skip not in removed_from_sack and skip.repoid != 'installed':
                        _remove_from_sack(skip)
            # Nothing was removed, so we still got a problem
             # the first time we get here we reset the resolved members of
             # tsInfo and takes a new run all members in the current transaction
            if not toRemove: 
                looping += 1
                if looping > 2:
                    break # Bail out
                else:
                    self.verbose_logger.debug('SKIPBROKEN: resetting already resolved packages (no packages to skip)' )
                    self.tsInfo.resetResolved(hard=True)
            rescode, restring = self.resolveDeps(True, skipping_broken=True)
            endTs = set(self.tsInfo)
             # Check if tsInfo has changes since we started to skip packages
             # if there is no changes then we got a loop.
             # the first time we get here we reset the resolved members of
             # tsInfo and takes a new run all members in the current transaction
            if startTs-endTs == set():
                looping += 1
                if looping > 2:
                    break # Bail out
                else:
                    self.verbose_logger.debug('SKIPBROKEN: resetting already resolved packages (transaction not changed)' )
                    self.tsInfo.resetResolved(hard=True)
            else: 
                # Reset the looping counter, because it is only a loop if the same transaction is
                # unchanged two times in row, not if it has been unchanged in a early stage.
                looping = 0 
                    
            # if we are all clear, then we have to check that the whole current transaction 
            # can complete the depsolve without error, because the packages skipped
            # can have broken something that passed the tests earlier.
            # FIXME: We need do this in a better way.
            if rescode != 1:
                self.verbose_logger.debug('SKIPBROKEN: sanity check the current transaction' )
                self.tsInfo.resetResolved(hard=True)
                self._checkMissingObsoleted() # This is totally insane, but needed :(
                self._checkUpdatedLeftovers() # Cleanup updated leftovers
                rescode, restring = self.resolveDeps()
        if rescode != 1:
            self.verbose_logger.debug("SKIPBROKEN: took %i rounds ", count)
            self.verbose_logger.info(_('\nPackages skipped because of dependency problems:'))
            skipped_list = [p for p in skipped_po]
            skipped_list.sort()
            for po in skipped_list:
                msg = _("    %s from %s") % (str(po),po.repo.id)
                self.verbose_logger.info(msg)
            self.skipped_packages.extend(skipped_list)   # make the skipped packages public
        else:
            # If we can't solve the problems the show the original error messages.
            self.verbose_logger.info("Skip-broken could not solve problems")
            return 1, orig_restring
        return rescode, restring

    def _add_not_found(self, pkgs, nevra_dict):
        if pkgs:
            return None

        pkgtup = (nevra_dict['name'], nevra_dict['arch'],
                  nevra_dict['epoch'], nevra_dict['version'],
                  nevra_dict['release'])
        if None in pkgtup:
            return None
        return pkgtup
    def _add_not_found_a(self, pkgs, nevra_dict={}, pkgtup=None):
        if pkgtup is None and nevra_dict:
            pkgtup = self._add_not_found(pkgs, nevra_dict)
        if pkgtup is None:
            return
        self._not_found_a[pkgtup] = YumNotFoundPackage(pkgtup)
    def _add_not_found_i(self, pkgs, nevra_dict={}, pkgtup=None):
        if pkgtup is None and nevra_dict:
            pkgtup = self._add_not_found(pkgs, nevra_dict)
        if pkgtup is None:
            return
        self._not_found_i[pkgtup] = YumNotFoundPackage(pkgtup)

    def _checkMissingObsoleted(self):
        """ 
        If multiple packages is obsoleting the same package
        then the TS_OBSOLETED can get removed from the transaction
        so we must make sure that they, exist and else create them
        """
        for txmbr in self.tsInfo.getMembersWithState(None, [TS_OBSOLETING,TS_OBSOLETED]):
            for pkg in txmbr.obsoletes:
                if not self.tsInfo.exists(pkg.pkgtup):
                    obs = self.tsInfo.addObsoleted(pkg,txmbr.po)
                    self.verbose_logger.debug('SKIPBROKEN: Added missing obsoleted %s (%s)' % (pkg,txmbr.po) )
            for pkg in txmbr.obsoleted_by:
                # check if the obsoleting txmbr is in the transaction
                # else remove the obsoleted txmbr
                # it clean out some really weird cases
                if not self.tsInfo.exists(pkg.pkgtup):
                    self.verbose_logger.debug('SKIPBROKEN: Remove extra obsoleted %s (%s)' % (txmbr.po,pkg) )
                    self.tsInfo.remove(txmbr.po.pkgtup)

    def _checkUpdatedLeftovers(self):
        """ 
        If multiple packages is updated the same package
        and this package get removed because of a dep issue
        then make sure that all the TS_UPDATED get removed.
        """
        for txmbr in self.tsInfo.getMembersWithState(None, [TS_UPDATED]):
            for pkg in txmbr.updated_by:
                # check if the updating txmbr is in the transaction
                # else remove the updated txmbr
                # it clean out some really weird cases with dupes installed on the system
                if not self.tsInfo.exists(pkg.pkgtup):
                    self.verbose_logger.debug('SKIPBROKEN: Remove extra updated %s (%s)' % (txmbr.po,pkg) )
                    self.tsInfo.remove(txmbr.po.pkgtup)

    def _getPackagesToRemoveAllArch(self,po):
        ''' get all compatible arch packages in pkgSack'''
        pkgs = []
        if self.arch.multilib:
            n,a,e,v,r = po.pkgtup
            # skip for all compat archs
            for a in self.arch.archlist:
                pkgtup = (n,a,e,v,r)
                matched = self.pkgSack.searchNevra(n,e,v,r,a) 
                pkgs.extend(matched)
        else:
            pkgs.append(po)
        return pkgs   
        
                
                
        

    def _skipFromTransaction(self,po):
        skipped =  []
        n,a,e,v,r = po.pkgtup
        # skip for all compat archs
        for a in self.arch.archlist:
            pkgtup = (n,a,e,v,r)
            if self.tsInfo.exists(pkgtup):
                for txmbr in self.tsInfo.getMembers(pkgtup):
                    pkg = txmbr.po
                    skip = self._removePoFromTransaction(pkg)
                    skipped.extend(skip)
        return skipped

    def _removePoFromTransaction(self,po):
        skip =  []
        if self.tsInfo.exists(po.pkgtup):
            self.verbose_logger.debug('SKIPBROKEN: removing %s from transaction' % str(po))
            self.tsInfo.remove(po.pkgtup)
            if not po.repoid == 'installed':
                skip.append(po)
        return skip 
              
    def _buildDepTree(self):
        ''' create a dictionary with po and deps '''
        depTree = { }
        for txmbr in self.tsInfo:
            for dep in txmbr.depends_on:
                depTree.setdefault(dep, []).append(txmbr.po)
        # self._printDepTree(depTree)
        return depTree

    def _printDepTree(self, tree):
        for pkg, l in tree.iteritems():
            print pkg
            for p in l:
                print "\t", p

    def _printTransaction(self):
        #transaction set states
        state = { TS_UPDATE     : "update",
                  TS_INSTALL    : "install",
                  TS_TRUEINSTALL: "trueinstall",
                  TS_ERASE      : "erase",
                  TS_OBSOLETED  : "obsoleted",
                  TS_OBSOLETING : "obsoleting",
                  TS_AVAILABLE  : "available",
                  TS_UPDATED    : "updated"}

        self.verbose_logger.log(logginglevels.DEBUG_2,"SKIPBROKEN: Current Transaction : %i member(s) " % len(self.tsInfo))
        for txmbr in sorted(self.tsInfo):
            msg = "SKIPBROKEN:  %-11s : %s " % (state[txmbr.output_state],txmbr.po)
            self.verbose_logger.log(logginglevels.DEBUG_2, msg)
            for po,rel in sorted(set(txmbr.relatedto)):
                msg = "SKIPBROKEN:                   %s : %s" % (rel,po)
                self.verbose_logger.log(logginglevels.DEBUG_2, msg)
        self.verbose_logger.log(logginglevels.DEBUG_2,"SKIPBROKEN:%s" % (60 * "="))
                                    
    def _getPackagesToRemove(self,po,deptree,toRemove):
        '''
        get the (related) pos to remove.
        '''
        toRemove.add(po)
        for txmbr in self.tsInfo.getMembers(po.pkgtup):
            for pkg in (txmbr.updates + txmbr.obsoletes):
                toRemove.add(pkg)
                self._getDepsToRemove(pkg, deptree, toRemove)
            # Remove related packages    
            for (relative, relation) in txmbr.relatedto:
                toRemove.add(relative)
                self._getDepsToRemove(relative, deptree, toRemove)                
        self._getDepsToRemove(po, deptree, toRemove)

    def _getDepsToRemove(self,po, deptree, toRemove):
        for dep in deptree.get(po, []): # Loop trough all deps of po
            more_deps = False
            for txmbr in self.tsInfo.getMembers(dep.pkgtup):
                txmbr.removeDep(po)
                if txmbr.depends_on:
                    more_deps = True
                    break

                for pkg in (txmbr.updates + txmbr.obsoletes):
                    toRemove.add(pkg)
            if more_deps: # Others depend on this pkg, so leave it. bz#905899
                continue
            if dep in toRemove: #  If this is true we inf. recurse, so check
                continue        # even though it shouldn't happen. bz#874065
            toRemove.add(dep)
            self._getDepsToRemove(dep, deptree, toRemove)

    def _rpmdb_warn_checks(self, out=None, warn=True, chkcmd=None, header=None,
                           ignore_pkgs=[]):
        if out is None:
            out = self.logger.warning
        if chkcmd is None:
            chkcmd = ['dependencies', 'duplicates']
        if header is None:
            # FIXME: _N()
            msg = _("** Found %d pre-existing rpmdb problem(s),"
                    " 'yum check' output follows:")
            header = lambda problems: not problems or out(msg % problems)
        if warn:
            out(_('Warning: RPMDB altered outside of yum.'))

        if type(chkcmd) in (type([]), type(set())):
            chkcmd = set(chkcmd)
        else:
            chkcmd = set([chkcmd])

        ignore_pkgtups = set((pkg.pkgtup for pkg in ignore_pkgs))

        rc = 0
        probs = []
        if chkcmd.intersection(set(('all', 'dependencies'))):
            prob2ui = {'requires' : _('missing requires'),
                       'conflicts' : _('installed conflict')}
            for prob in self.rpmdb.check_dependencies():
                if prob.pkg.pkgtup in ignore_pkgtups:
                    continue
                if prob.problem == 'conflicts':
                    found = True # all the conflicting pkgs have to be ignored
                    for res in prob.conflicts:
                        if res.pkgtup not in ignore_pkgtups:
                            found = False
                            break
                    if found:
                        continue
                probs.append(prob)

        if chkcmd.intersection(set(('all', 'duplicates'))):
            iopkgs = set(self.conf.installonlypkgs)
            for prob in self.rpmdb.check_duplicates(iopkgs):
                if prob.pkg.pkgtup in ignore_pkgtups:
                    continue
                if prob.duplicate.pkgtup in ignore_pkgtups:
                    continue
                probs.append(prob)

        if chkcmd.intersection(set(('all', 'obsoleted'))):
            for prob in self.rpmdb.check_obsoleted():
                if prob.pkg.pkgtup in ignore_pkgtups:
                    continue
                if prob.obsoleter.pkgtup in ignore_pkgtups:
                    continue
                probs.append(prob)

        if chkcmd.intersection(set(('all', 'provides'))):
            for prob in self.rpmdb.check_provides():
                if prob.pkg.pkgtup in ignore_pkgtups:
                    continue
                probs.append(prob)

        header(len(probs))
        for prob in sorted(probs):
            out(prob)

        return probs

    def runTransaction(self, cb):
        """Perform the transaction.

        :param cb: an rpm callback object to use in the transaction
        :return: a :class:`yum.misc.GenericHolder` containing
           information about the results of the transaction
        :raises: :class:`yum.Errors.YumRPMTransError` if there is a
           transaction cannot be completed
        """

        def create_snapshot(post=False):
            """Create the pre or post trans snapshot if we have free space."""
            msg = _("Not enough space on logical volumes to create %s FS snapshot." %
                    ("post trans" if post else "pre."))
            try:
                has_space = self.fssnap.has_space(self.conf.fssnap_percentage)
            except LibLVMError as e:
                msg = _("Could not determine free space on logical volumes: ") + lvmerr2str(e)
                has_space = False
            if not has_space:
                if not post and self.conf.fssnap_abort_on_errors in ('snapshot-failure', 'any'):
                    raise Errors.YumRPMTransError(msg="Aborting transaction", errors=msg)
                else:
                    self.verbose_logger.critical(msg)
            else:
                tags = {'*': ['reason=automatic']} # FIXME: pre. and post tags
                msg = _("Failed to create snapshot")
                try:
                    snaps = self.fssnap.snapshot(self.conf.fssnap_percentage, tags=tags)
                except LibLVMError as e:
                    msg += ": " + lvmerr2str(e)
                    snaps = []
                if not snaps:
                    if not post and self.conf.fssnap_abort_on_errors in ('snapshot-failure', 'any'):
                        raise Errors.YumRPMTransError(msg="Aborting transaction", errors=msg)
                    else:
                        self.verbose_logger.critical(msg)
                for (odev, ndev) in snaps:
                    self.verbose_logger.info(_("Created snapshot from %s, results is: %s") % (odev, ndev))

        if (self.conf.fssnap_automatic_pre or self.conf.fssnap_automatic_post) and not self.fssnap.available:
            msg = _("Snapshot support not available.")
            if self.conf.fssnap_abort_on_errors in ('broken-setup', 'any'):
                raise Errors.YumRPMTransError(msg="Aborting transaction.", errors=msg)
            else:
                self.verbose_logger.critical(msg)

        if self.fssnap.available and ((self.conf.fssnap_automatic_pre or
                                       self.conf.fssnap_automatic_post) and
                                      self.conf.fssnap_automatic_keep):
            # Automatically kill old snapshots...
            cleanup_fail = False
            try:
                snaps = self.fssnap.old_snapshots()
            except LibLVMError as e:
                self.verbose_logger.debug(lvmerr2str(e))
                cleanup_fail = True
                snaps = []
            snaps = sorted(snaps, key=lambda x: (x['ctime'], x['origin_dev']),
                           reverse=True)
            last = '<n/a>'
            num = 0
            todel = []
            for snap in snaps:
                num += 1

                if last != snap['origin_dev']:
                    last = snap['origin_dev']
                    num = 1
                    continue

                if num > self.conf.fssnap_automatic_keep:
                    todel.append(snap['dev'])
            # Display something to the user?
            try:
                snaps = self.fssnap.del_snapshots(devices=todel)
            except LibLVMError as e:
                self.verbose_logger.debug(lvmerr2str(e))
                cleanup_fail = True
                snaps = []
            if len(snaps):
                self.verbose_logger.info(_("Deleted %u snapshots.") % len(snaps))
            elif cleanup_fail:
                self.verbose_logger.warning(_("Skipping the cleanup of old "
                                              "snapshots due to errors"))

        if (self.fssnap.available and
            (not self.ts.isTsFlagSet(rpm.RPMTRANS_FLAG_TEST) and
            self.conf.fssnap_automatic_pre)):
            create_snapshot()

        self.plugins.run('pretrans')

        #  We may want to put this other places, eventually, but for now it's
        # good as long as we get it right for history.
        for repo in self.repos.listEnabled():
            if repo._xml2sqlite_local:
                self.run_with_package_names.add('yum-metadata-parser')
                break

        if (not self.conf.history_record or
            self.ts.isTsFlagSet(rpm.RPMTRANS_FLAG_TEST)):
            frpmdbv = self.tsInfo.futureRpmDBVersion()
        else:
            using_pkgs_pats = list(self.run_with_package_names)
            using_pkgs = self.rpmdb.returnPackages(patterns=using_pkgs_pats)
            rpmdbv  = self.rpmdb.simpleVersion(main_only=True)[0]
            lastdbv = self.history.last()
            if lastdbv is not None:
                lastdbv = lastdbv.end_rpmdbversion
            rpmdb_problems = []
            if lastdbv is None or rpmdbv != lastdbv:
                txmbrs = self.tsInfo.getMembersWithState(None, TS_REMOVE_STATES)
                ignore_pkgs = [txmbr.po for txmbr in txmbrs]
                output_warn = lastdbv is not None
                rpmdb_problems = self._rpmdb_warn_checks(warn=output_warn,
                                                        ignore_pkgs=ignore_pkgs)
            cmdline = None
            if hasattr(self, 'args') and self.args:
                cmdline = ' '.join(self.args)
            elif hasattr(self, 'cmds') and self.cmds:
                cmdline = ' '.join(self.cmds)

            frpmdbv = self.tsInfo.futureRpmDBVersion()
            self.history.beg(rpmdbv, using_pkgs, list(self.tsInfo),
                             self.skipped_packages, rpmdb_problems, cmdline)
            # write out our config and repo data to additional history info
            self._store_config_in_history()
            if hasattr(self, '_shell_history_write'): # Only in cli...
                self._shell_history_write()
            
            self.plugins.run('historybegin')
        #  Just before we update the transaction, update what we think the
        # rpmdb will look like. This needs to be done before the run, so that if
        # "something" happens and the rpmdb is different from what we think it
        # will be we store what we thought, not what happened (so it'll be an
        # invalid cache).
        self.rpmdb.transactionResultVersion(frpmdbv)
        # transaction has started - all bets are off on our saved ts file
        if self._ts_save_file is not None:
            # write the saved transaction data to the addon location in history
            # so we can pull it back later if we need to
            savetx_msg = open(self._ts_save_file, 'r').read()
            self.history.write_addon_data('saved_tx', savetx_msg)

            try:
                os.unlink(self._ts_save_file)
            except (IOError, OSError), e:
                pass
        self._ts_save_file = None
        
        if self.conf.reset_nice:
            onice = os.nice(0)
            if onice:
                try:
                    os.nice(-onice)
                except:
                    onice = 0

        errors = self.ts.run(cb.callback, '')
        # ts.run() exit codes are, hmm, "creative": None means all ok, empty 
        # list means some errors happened in the transaction and non-empty 
        # list that there were errors preventing the ts from starting...
        if self.conf.reset_nice:
            try:
                os.nice(onice)
            except:
                pass
        
        # make resultobject - just a plain yumgenericholder object
        resultobject = misc.GenericHolder()
        resultobject.return_code = 0
        if errors is None:
            pass
        elif len(errors) == 0:
            errstring = _('Warning: scriptlet or other non-fatal errors occurred during transaction.')
            self.verbose_logger.debug(errstring)
            resultobject.return_code = 1
        else:
            if self.conf.history_record and not self.ts.isTsFlagSet(rpm.RPMTRANS_FLAG_TEST):
                herrors = [to_unicode(to_str(x)) for x in errors]
                self.plugins.run('historyend')                
                self.history.end(rpmdbv, 2, errors=herrors)

                
            self.logger.critical(_("Transaction couldn't start:"))
            for e in errors:
                self.logger.critical(e[0]) # should this be 'to_unicoded'?
            raise Errors.YumRPMTransError(msg=_("Could not run transaction."),
                                          errors=errors)

                          
        if not self.ts.isTsFlagSet(rpm.RPMTRANS_FLAG_TEST):
            self.cleanUsedHeadersPackages()
            if not self.conf.keepcache and self._cashe:
                self._cashe.cleanup()
        
        for i in ('ts_all_fn', 'ts_done_fn'):
            if hasattr(cb, i):
                fn = getattr(cb, i)
                try:
                    misc.unlink_f(fn)
                except (IOError, OSError), e:
                    self.logger.critical(_('Failed to remove transaction file %s') % fn)

        
        # drop out the rpm cache so we don't step on bad hdr indexes
        if (self.ts.isTsFlagSet(rpm.RPMTRANS_FLAG_TEST) or
            resultobject.return_code):
            self.rpmdb.dropCachedData()
        else:
            self.rpmdb.dropCachedDataPostTransaction(list(self.tsInfo))

        self.plugins.run('posttrans')
        # sync up what just happened versus what is in the rpmdb
        if not self.ts.isTsFlagSet(rpm.RPMTRANS_FLAG_TEST):
            vTcb = None
            if hasattr(cb, 'verify_txmbr'):
                vTcb = cb.verify_txmbr
            self.verifyTransaction(resultobject, vTcb)
            if self.conf.group_command == 'objects':
                self.igroups.save()

        if (self.fssnap.available and
            (not self.ts.isTsFlagSet(rpm.RPMTRANS_FLAG_TEST) and
            self.conf.fssnap_automatic_post)):
            create_snapshot(post=True)
        return resultobject

    def verifyTransaction(self, resultobject=None, txmbr_cb=None):
        """Check that the transaction did what was expected, and 
        propagate external yumdb information.  Output error messages
        if the transaction did not do what was expected.

        :param resultobject: the :class:`yum.misc.GenericHolder`
           object returned from the :func:`runTransaction` call that
           ran the transaction
        :param txmbr_cb: the callback for the rpm transaction members
        """
        # check to see that the rpmdb and the tsInfo roughly matches
        # push package object metadata outside of rpmdb into yumdb
        # delete old yumdb metadata entries
        
        # for each pkg in the tsInfo
        # if it is an install - see that the pkg is installed
        # if it is a remove - see that the pkg is no longer installed, provided
        #    that there is not also an install of this pkg in the tsInfo (reinstall)
        # for any kind of install add from_repo to the yumdb, and the cmdline
        # and the install reason

        def _call_txmbr_cb(txmbr, count):
            if txmbr_cb is not None:
                count += 1
                txmbr_cb(txmbr, count)
            return count
        
        oil = self.conf.override_install_langs
        if not oil:
            oil = rpm.expandMacro("%_install_langs")
        if oil == 'all':
            oil = ''
        elif oil:
            oil = ":".join(sorted(oil.split(':')))
        vt_st = time.time()
        self.plugins.run('preverifytrans')
        count = 0
        for txmbr in self.tsInfo:
            if txmbr.output_state in TS_INSTALL_STATES:
                if not self.rpmdb.contains(po=txmbr.po):
                    # maybe a file log here, too
                    # but raising an exception is not going to do any good
                    self.logger.critical(_('%s was supposed to be installed' \
                                           ' but is not!') % txmbr.po)
                    # Note: Get Panu to do te.Failed() so we don't have to
                    txmbr.output_state = TS_FAILED
                    count = _call_txmbr_cb(txmbr, count)
                    continue
                count = _call_txmbr_cb(txmbr, count)
                po = self.getInstalledPackageObject(txmbr.pkgtup)
                rpo = txmbr.po
                po.yumdb_info.from_repo = rpo.repoid
                po.yumdb_info.reason = txmbr.reason
                po.yumdb_info.releasever = self.conf.yumvar['releasever']
                for var in self.conf.yumvar: # Store all yum variables.
                    # Skip some of the variables...
                    if var == 'releasever': continue
                    if var == 'basearch': continue # This "never" changes.
                    if var == 'arch':     continue
                    if var == 'uuid':     continue
                    setattr(po.yumdb_info, 'var_' + var, self.conf.yumvar[var])
                if oil:
                    po.yumdb_info.ts_install_langs = oil
                if 'nocontexts' in self.conf.tsflags:
                    po.yumdb_info.tsflag_nocontexts = 'true'
                if 'nodocs' in self.conf.tsflags:
                    po.yumdb_info.tsflag_nodocs = 'true'
                if 'noscripts' in self.conf.tsflags:
                    po.yumdb_info.tsflag_noscripts = 'true'
                if 'notriggers' in self.conf.tsflags:
                    po.yumdb_info.tsflag_notriggers = 'true'

                if hasattr(self, 'args') and self.args:
                    po.yumdb_info.command_line = ' '.join(self.args)
                elif hasattr(self, 'cmds') and self.cmds:
                    po.yumdb_info.command_line = ' '.join(self.cmds)
                csum = rpo.returnIdSum()
                if csum is not None:
                    po.yumdb_info.checksum_type = str(csum[0])
                    po.yumdb_info.checksum_data = str(csum[1])

                if isinstance(rpo, YumLocalPackage):
                    try:
                        st = os.stat(rpo.localPkg())
                        lp_ctime = str(int(st.st_ctime))
                        lp_mtime = str(int(st.st_mtime))
                        po.yumdb_info.from_repo_revision  = lp_ctime
                        po.yumdb_info.from_repo_timestamp = lp_mtime
                    except: pass

                if rpo.xattr_origin_url is not None:
                    po.yumdb_info.origin_url = rpo.xattr_origin_url

                if hasattr(rpo.repo, 'repoXML'):
                    md = rpo.repo.repoXML
                    if md and md.revision is not None:
                        po.yumdb_info.from_repo_revision  = str(md.revision)
                    if md:
                        po.yumdb_info.from_repo_timestamp = str(md.timestamp)

                if hasattr(txmbr, 'group_member'):
                    # FIXME:
                    po.yumdb_info.group_member = txmbr.group_member

                loginuid = misc.getloginuid()
                if txmbr.updates or txmbr.downgrades or txmbr.reinstall:
                    if txmbr.updates:
                        opo = txmbr.updates[0]
                    elif txmbr.downgrades:
                        opo = txmbr.downgrades[0]
                    else:
                        opo = po
                    if 'installed_by' in opo.yumdb_info:
                        po.yumdb_info.installed_by = opo.yumdb_info.installed_by
                    if 'group_member' in opo.yumdb_info:
                        po.yumdb_info.group_member = opo.yumdb_info.group_member
                    if loginuid is not None:
                        po.yumdb_info.changed_by = str(loginuid)
                elif loginuid is not None:
                    po.yumdb_info.installed_by = str(loginuid)

                if self.conf.history_record:
                    self.history.sync_alldb(po)

        # Remove old ones after installing new ones, so we can copy values.
        for txmbr in self.tsInfo:
            if txmbr.output_state in TS_INSTALL_STATES:
                pass
            elif txmbr.output_state in TS_REMOVE_STATES:
                if self.rpmdb.contains(po=txmbr.po):
                    if not self.tsInfo.getMembersWithState(pkgtup=txmbr.pkgtup,
                                output_states=TS_INSTALL_STATES):
                        # maybe a file log here, too
                        # but raising an exception is not going to do any good
                        # Note: This actually triggers atm. because we can't
                        #       always find the erased txmbr to set it when
                        #       we should.
                        self.logger.critical(_('%s was supposed to be removed' \
                                               ' but is not!' % txmbr.po))
                        # Note: Get Panu to do te.Failed() so we don't have to
                        txmbr.output_state = TS_FAILED
                        count = _call_txmbr_cb(txmbr, count)
                        continue
                count = _call_txmbr_cb(txmbr, count)
                yumdb_item = self.rpmdb.yumdb.get_package(po=txmbr.po)
                yumdb_item.clean()
            else:
                count = _call_txmbr_cb(txmbr, count)
                self.verbose_logger.log(logginglevels.DEBUG_2, 'What is this? %s' % txmbr.po)

        self.plugins.run('postverifytrans')
        rpmdbv = self.rpmdb.simpleVersion(main_only=True)[0]
        if self.conf.history_record and not self.ts.isTsFlagSet(rpm.RPMTRANS_FLAG_TEST):
            ret = -1
            if resultobject is not None:
                ret = resultobject.return_code
            self.plugins.run('historyend')
            self.history.end(rpmdbv, ret)
        self.rpmdb.dropCachedData()
        self.verbose_logger.debug('VerifyTransaction time: %0.3f' % (time.time() - vt_st))

    def costExcludePackages(self):
        """Create an excluder for repositories with higher costs. For
        example, if repo-A:cost=1 and repo-B:cost=2, this function
        will set up an excluder on repo-B that looks for packages in
        repo-B.
        """
        # if all the repo.costs are equal then don't bother running things
        costs = {}
        for r in self.repos.listEnabled():
            costs.setdefault(r.cost, []).append(r)

        if len(costs) <= 1:
            return

        done = False
        exid = "yum.costexcludes"
        orepos = []
        for cost in sorted(costs):
            if done: # Skip the first one, as they have lowest cost so are good.
                for repo in costs[cost]:
                    yce = _YumCostExclude(repo, self.repos)
                    repo.sack.addPackageExcluder(repo.id, exid,
                                                 'exclude.pkgtup.in', yce)
            orepos.extend(costs[cost])
            done = True

    def excludePackages(self, repo=None):
        """Remove packages from packageSacks based on global exclude
        lists, command line excludes and per-repository excludes.

        :param repo: a repo object to use.  If not given, all
           repositories are used
        """
        if "all" in self.conf.disable_excludes:
            return
        
        # if not repo: then assume global excludes, only
        # if repo: then do only that repos' packages and excludes
        
        if not repo: # global only
            if "main" in self.conf.disable_excludes:
                return
            excludelist = self.conf.exclude
            repoid = None
            exid_beg = 'yum.excludepkgs'
        else:
            if repo.id in self.conf.disable_excludes:
                return
            excludelist = repo.getExcludePkgList()
            repoid = repo.id
            exid_beg = 'yum.excludepkgs.' + repoid

        count = 0
        for match in excludelist:
            count += 1
            exid = "%s.%u" % (exid_beg, count)
            self.pkgSack.addPackageExcluder(repoid, exid,'exclude.match', match)

    def includePackages(self, repo):
        """Remove packages from packageSacks based on list of
        packages to include.  

        :param repo: the repository to use
        """
        for di in getattr(self.conf, 'disable_includes', []):
            if di == 'all' or di == repo.id:
                return
        includelist = repo.getIncludePkgList()
        
        if len(includelist) == 0:
            return
        
        # includepkgs actually means "exclude everything that doesn't match".
        #  So we mark everything, then wash those we want to keep and then
        # exclude everything that is marked.
        exid = "yum.includepkgs.1"
        self.pkgSack.addPackageExcluder(repo.id, exid, 'mark.washed')
        count = 0
        for match in includelist:
            count += 1
            exid = "%s.%u" % ("yum.includepkgs.2", count)
            self.pkgSack.addPackageExcluder(repo.id, exid, 'wash.match', match)
        exid = "yum.includepkgs.3"
        self.pkgSack.addPackageExcluder(repo.id, exid, 'exclude.marked')
        
    def doLock(self, lockfile = YUM_PID_FILE):
        """Acquire the yum lock.

        :param lockfile: the file to use for the lock
        :raises: :class:`yum.Errors.LockError`
        """
        if self.conf.uid != 0:
            #  If we are a user, assume we are using the root cache ... so don't
            # bother locking.
            if self.conf.cache:
                return
            root = self.conf.cachedir
            # Don't want <cachedir>/var/run/yum.pid ... just: <cachedir>/yum.pid
            lockfile = os.path.basename(lockfile)
        else:
            root = self.conf.installroot
        lockfile = root + '/' + lockfile # lock in the chroot
        lockfile = os.path.normpath(lockfile) # get rid of silly preceding extra /
        
        mypid=str(os.getpid())    
        while True:
            ret = self._lock(lockfile, mypid, 0644)
            if ret:
                break

            oldpid = self._get_locker(lockfile)
            if not oldpid:
                # Invalid locker: unlink lockfile and retry
                self._unlock(lockfile)
                continue
            if oldpid == os.getpid(): # if we own the lock, we're fine
                ret = 1
                break
            # Another copy seems to be running.
            msg = _('Existing lock %s: another copy is running as pid %s.') % (lockfile, oldpid)
            raise Errors.LockError(0, msg, oldpid)

        if ret == 2:
            #  Means lockdir isn't setup, out of bad options just run without
            # locks.
            return

        assert ret == 1
        # We've got the lock, store it so we can auto-unlock on __del__...
        self._lockfile = lockfile
    
    def doUnlock(self, lockfile=None):
        """Release the yum lock.

        :param lockfile: the lock file to use.  If not given, the file
           that was given as a parameter to the :func:`doLock` call
           that closed the lock is used
        """
        # if we're not root then we don't lock - just return nicely
        #  Note that we can get here from __del__, so if we haven't created
        # YumBase.conf we don't want to do so here as creating stuff inside
        # __del__ is bad.
        if hasattr(self, 'preconf'):
            return

        #  Obviously, we can't lock random places as non-root, but we still want
        # to get rid of our lock file. Given we now have _lockfile I'm pretty
        # sure nothing should ever pass lockfile in here anyway.
        if self.conf.uid != 0:
            lockfile = None
        
        if lockfile is not None:
            root = self.conf.installroot
            lockfile = root + '/' + lockfile # lock in the chroot
        elif self._lockfile is None:
            return # Don't delete other people's lock files on __del__
        else:
            lockfile = self._lockfile # Get the value we locked with
        
        self._unlock(lockfile)
        self._lockfile = None
        
    @staticmethod
    def _lock(filename, contents='', mode=0777):
        lockdir = os.path.dirname(filename)
        try:
            if not os.path.exists(lockdir):
                #  We used to os.makedirs(lockdir, mode=0755) ... but that
                # causes problems now due to /var/run being a magic systemd dir.
                #  So we now just give up and run, hopefully nobody runs N
                # instances before the magic dir. is activate.
                return 2

            fd = os.open(filename, os.O_EXCL|os.O_CREAT|os.O_WRONLY, mode)    
            os.write(fd, contents)
            os.close(fd)
            return 1
        except OSError, msg:
            if not msg.errno == errno.EEXIST: 
                # Whoa. What the heck happened?
                errmsg = _('Could not create lock at %s: %s ') % (filename, exception2msg(msg))
                raise Errors.LockError(msg.errno, errmsg, int(contents))
            return 0
    
    @staticmethod
    def _unlock(filename):
        misc.unlink_f(filename)

    @staticmethod
    def _get_locker(lockfile):
        try: fd = open(lockfile, 'r')
        except (IOError, OSError), e:
            msg = _("Could not open lock %s: %s") % (lockfile, e)
            raise Errors.LockError(errno.EPERM, msg)
        try: oldpid = int(fd.readline())
        except ValueError:
            return None # Bogus pid

        try:
            stat = open("/proc/%d/stat" % oldpid).readline()
            if stat.split()[2] == 'Z':
                return None # The pid is a zombie
        except IOError:
            # process dead or /proc not mounted
            try: os.kill(oldpid, 0)
            except OSError, e:
                if e[0] == errno.ESRCH:
                    return None # The pid doesn't exist
                # Whoa. What the heck happened?
                msg = _('Unable to check if PID %s is active') % oldpid
                raise Errors.LockError(errno.EPERM, msg, oldpid)
        return oldpid

    def verifyPkg(self, fo, po, raiseError):
        """Check that the checksum of a remote package matches what we
        expect it to be.  If the checksum of the package file is
        wrong, and the file is also larger than expected, it cannot be
        redeemed, so delete it.

        :param fo: the file object of the package
        :param po: the package object to verify
        :param raiseError: if *raiseError* is 1, and the package
           does not check out, a :class:`URLGrabError will be raised.
           Defaults to 0
        :return: True if the package is verified successfully.
           Otherwise, False will be returned, unless *raiseError* is
           1, in which case a :class:`URLGrabError` will be raised
        :raises: :class:`URLGrabError` if verification fails, and
           *raiseError* is 1
        """
        failed = False

        if type(fo) is types.InstanceType:
            fo = fo.filename
        
        if fo != po.localPkg():
            po.localpath = fo

        if not po.verifyLocalPkg():
            failed = True
        else:
            ylp = YumLocalPackage(self.rpmdb.readOnlyTS(), fo)
            if ylp.pkgtup != po.pkgtup:
                failed = True


        if failed:            
            # if the file is wrong AND it is >= what we expected then it
            # can't be redeemed. If we can, kill it and start over fresh
            cursize = os.stat(fo)[6]
            totsize = long(po.size)
            if cursize >= totsize and not po.repo.cache:
                # if the path to the file is NOT inside the pkgdir then don't
                # unlink it b/c it is probably a file:// url and possibly not
                # unlinkable
                if fo.startswith(po.repo.pkgdir):
                    os.unlink(fo)

            if raiseError:
                msg = _('Package does not match intended download. Suggestion: run yum --enablerepo=%s clean metadata and try again') %  po.repo.id
                raise URLGrabError(-1, msg)
            else:
                return False

        
        return True
        
        
    def verifyChecksum(self, fo, checksumType, csum):
        """Verify that the checksum of the given file matches the
        given checksum.

        :param fo: the file object to verify the checksum of
        :param checksumType: the type of checksum to use
        :parm csum: the checksum to check against
        :return: 0 if the checksums match
        :raises: :class:`URLGrabError` if there is an error performing
           the checksums, or the checksums do not match
        """
        try:
            filesum = misc.checksum(checksumType, fo)
        except Errors.MiscError, e:
            raise URLGrabError(-3, _('Could not perform checksum'))
            
        if filesum != csum:
            raise URLGrabError(-1, _('Package does not match checksum'))
        
        return 0

    def downloadPkgs(self, pkglist, callback=None, callback_total=None):
        """Download the packages specified by the given list of
        package objects.

        :param pkglist: a list of package objects specifying the
           packages to download
        :param callback: unused
        :param callback_total: a callback to output messages about the
           download operation
        :return: a dictionary containing errors from the downloading process
        :raises: :class:`URLGrabError`
        """
        def mediasort(apo, bpo):
            # FIXME: we should probably also use the mediaid; else we
            # could conceivably ping-pong between different disc1's
            a = apo.getDiscNum()
            b = bpo.getDiscNum()
            if a is None and b is None:
                # deltas first to start rebuilding asap
                return cmp(isinstance(bpo, DeltaPackage),
                           isinstance(apo, DeltaPackage)) or cmp(apo, bpo)
            if a is None:
                return -1
            if b is None:
                return 1
            if a < b:
                return -1
            elif a > b:
                return 1
            return 0
        
        errors = {}
        def adderror(po, msg):
            errors.setdefault(po, []).append(msg)
            if po.localpath.endswith('.tmp'):
                misc.unlink_f(po.localpath) # won't resume this..

        #  We close the history DB here because some plugins (presto) use
        # threads. And sqlite really doesn't like threads. And while I don't
        # think it should matter, we've had some reports of history DB
        # corruption, and it was implied that it happened just after C-c
        # at download time and this is a safe thing to do.
        #  Note that manual testing shows that history is not connected by
        # this point, from the cli with no plugins. So this really does
        # nothing *sigh*.
        self.history.close()

        self.plugins.run('predownload', pkglist=pkglist)
        beenthere = set() # only once, please. BZ 468401
        downloadonly = getattr(self.conf, 'downloadonly', False)
        remote_pkgs = []
        remote_size = 0

        def verify_local(po):
            local = po.localPkg()
            if local in beenthere:
                # This is definitely a depsolver bug.  Make it fatal?
                self.verbose_logger.warn(_("ignoring a dupe of %s") % po)
                return True
            beenthere.add(local)
            if downloadonly and not os.path.exists(local):
              # Check before we munge the name...
              po.repo._preload_pkg_from_system_cache(po)
            if os.path.exists(local):
                if self.verifyPkg(local, po, False):
                    self.verbose_logger.debug(_("using local copy of %s") % po)
                    return True
                if po.repo.cache:
                    adderror(po, _('package fails checksum but caching is '
                        'enabled for %s') % po.repo.id)
                    return False
            if downloadonly:
                po.localpath += '.%d.tmp' % os.getpid()
                try: os.rename(local, po.localpath)
                except OSError: pass
                po.returnIdSum()
                po.basepath # prefetch now; fails when repos are closed
            return False

        pkgs = []
        for po in pkglist:
            if hasattr(po, 'pkgtype') and po.pkgtype == 'local':
                continue
            if verify_local(po):
                continue
            if errors:
                return errors
            pkgs.append(po)

        # download presto metadata and use drpms
        presto = DeltaInfo(self, pkgs, adderror)
        deltasize = rpmsize = 0
        for po in pkgs:
            if isinstance(po, DeltaPackage):
                if verify_local(po):
                    # there's .drpm already, use it
                    presto.rebuild(po)
                    continue
                deltasize += po.size
                rpmsize += po.rpm.size
            remote_pkgs.append(po)
            remote_size += po.size
        if deltasize:
            self.verbose_logger.info(_('Delta RPMs reduced %s of updates to %s (%d%% saved)'),
                format_number(rpmsize), format_number(deltasize), 100 - deltasize*100.0/rpmsize)

        if downloadonly:
            if hasattr(self, '_old_cachedir'):
              # Try to link/copy them out, if we have somewhere to put them.

              for po in pkglist:
                if not po.localpath.startswith(self.conf.cachedir):
                  continue

                end = po.localpath[len(self.conf.cachedir):]
                try:
                  os.link(po.localpath, self._old_cachedir + end)
                except:
                  try:
                    shutil.copy2(po.localpath, self._old_cachedir + end)
                  except:
                    pass

            # close DBs, unlock
            self.repos.close()
            self.closeRpmDB()
            self.doUnlock()

        beg_download = time.time()
        all_remote_pkgs = remote_pkgs
        all_remote_size = remote_size
        while True:
            remote_pkgs.sort(mediasort)
            #  This is kind of a hack and does nothing in non-Fedora versions,
            # we'll fix it one way or anther soon.
            if (hasattr(urlgrabber.progress, 'text_meter_total_size') and
                len(remote_pkgs) > 1):
                urlgrabber.progress.text_meter_total_size(remote_size)
            i = 0
            local_size = [0]
            done_repos = set()
            async = hasattr(urlgrabber.grabber, 'parallel_wait')
            for po in remote_pkgs:
                i += 1

                def checkfunc(obj, po=po):
                    self.verifyPkg(obj, po, 1)
                    if po.localpath.endswith('.tmp'):
                        rpmfile = po.localpath.rsplit('.', 2)[0]
                        os.rename(po.localpath, rpmfile)
                        po.localpath = rpmfile
                    local_size[0] += po.size
                    if hasattr(urlgrabber.progress, 'text_meter_total_size'):
                        urlgrabber.progress.text_meter_total_size(remote_size,
                                                                  local_size[0])
                    if isinstance(po, DeltaPackage):
                        presto.rebuild(po)
                        return
                    else:
                        presto.dequeue_max()

                    if po.repoid not in done_repos:
                        done_repos.add(po.repoid)
                        #  Check a single package per. repo. ... to give a hint to
                        # the user on big downloads.
                        result, errmsg = self.sigCheckPkg(po)
                        if result != 0:
                            self.verbose_logger.warn("%s", errmsg)
                    po.localpath = obj.filename
                    if po in errors:
                        del errors[po]

                text = os.path.basename(po.relativepath)
                kwargs = {}
                if async and po.repo._async:
                    kwargs['failfunc'] = lambda obj, po=po: adderror(po, exception2msg(obj.exception))
                    kwargs['async'] = True
                elif not (i == 1 and not local_size[0] and remote_size == po.size):
                    text = '(%s/%s): %s' % (i, len(remote_pkgs), text)
                try:
                    po.repo.getPackage(po,
                                       checkfunc=checkfunc,
                                       text=text,
                                       cache=po.repo.http_caching != 'none',
                                       **kwargs
                                       )
                except Errors.RepoError, e:
                    adderror(po, exception2msg(e))
            if async:
                try:
                    urlgrabber.grabber.parallel_wait()
                except KeyboardInterrupt:
                    for po in remote_pkgs:
                        if po.localpath.endswith('.tmp'):
                            misc.unlink_f(po.localpath)
                        elif isinstance(po, DeltaPackage) and po.rpm.localpath.endswith('.tmp'):
                            misc.unlink_f(po.rpm.localpath)
                    raise
            presto.dequeue_all()
            presto.wait()

            if hasattr(urlgrabber.progress, 'text_meter_total_size'):
                urlgrabber.progress.text_meter_total_size(0)

            fatal = False
            for po in errors:
                if not isinstance(po, DeltaPackage):
                    fatal = True
                    break
            if not errors or fatal:
                break

            # there were drpm related errors *only*
            remote_pkgs = []
            remote_size = 0
            for po in errors:
                po = po.rpm
                remote_pkgs.append(po)
                remote_size += po.size
            # callback_total needs the total pkg count
            all_remote_pkgs.extend(remote_pkgs)
            all_remote_size += remote_size
            errors.clear()
            self.verbose_logger.warn(_('Some delta RPMs failed to download or rebuild. Retrying..'))
        if callback_total and not errors:
            callback_total(all_remote_pkgs, all_remote_size, beg_download)

        if not downloadonly:
            # XXX: Run unlocked?  Skip this for now..
            self.plugins.run('postdownload', pkglist=pkglist, errors=errors)

        # Close curl object after we've downloaded everything.
        if hasattr(urlgrabber.grabber, 'reset_curl_obj'):
            urlgrabber.grabber.reset_curl_obj()

        if downloadonly and not errors: # caller handles errors
            self.verbose_logger.info(_('exiting because "Download Only" specified'))
            sys.exit(self.exit_code)
        return errors

    def verifyHeader(self, fo, po, raiseError):
        """Check that the header of the given file object and matches
        the given package.

        :param fo: the file object to check
        :param po: the package object to check
        :param raiseError: if *raiseError* is True, a
           :class:`URLGrabError` will be raised if the header matches
           the package object, or cannot be read from the file.  If
           *raiseError* is False, 0 will be returned in the above
           cases
        :return: 1 if the header matches the package object, and 0 if
           they do not match, and *raiseError* is False
        :raises: :class:`URLGrabError` if *raiseError* is True, and
           the header does not match the package object or cannot be
           read from the file
        """
        if type(fo) is types.InstanceType:
            fo = fo.filename
            
        try:
            hlist = rpm.readHeaderListFromFile(fo)
            hdr = hlist[0]
        except (rpm.error, IndexError):
            if raiseError:
                raise URLGrabError(-1, _('Header is not complete.'))
            else:
                return 0
                
        yip = YumInstalledPackage(hdr) # we're using YumInstalledPackage b/c
                                       # it takes headers <shrug>
        if yip.pkgtup != po.pkgtup:
            if raiseError:
                raise URLGrabError(-1, 'Header does not match intended download')
            else:
                return 0
        
        return 1
        
    def downloadHeader(self, po):
        """Download a header from a package object.

        :param po: the package object to download the header from
        :raises: :class:`yum.Errors.RepoError` if there are errors
           obtaining the header
        """
        if hasattr(po, 'pkgtype') and po.pkgtype == 'local':
            return
                
        errors = {}
        local =  po.localHdr()
        repo = self.repos.getRepo(po.repoid)
        if os.path.exists(local):
            try:
                result = self.verifyHeader(local, po, raiseError=1)
            except URLGrabError, e:
                # might add a check for length of file - if it is < 
                # required doing a reget
                misc.unlink_f(local)
            else:
                po.hdrpath = local
                return
        else:
            if self.conf.cache:
                raise Errors.RepoError(_('Header not in local cache and caching-only mode enabled. Cannot download %s') % po.hdrpath,
                                       repo=repo)
        
        if self.dsCallback: self.dsCallback.downloadHeader(po.name)
        
        try:
            if not os.path.exists(repo.hdrdir):
                os.makedirs(repo.hdrdir)
            checkfunc = (self.verifyHeader, (po, 1), {})
            hdrpath = repo.getHeader(po, checkfunc=checkfunc,
                    cache=repo.http_caching != 'none',
                    )
        except Errors.RepoError, e:
            saved_repo_error = e
            try:
                misc.unlink_f(local)
            except OSError, e:
                raise Errors.RepoError, saved_repo_error
            else:
                raise Errors.RepoError, saved_repo_error
        else:
            po.hdrpath = hdrpath
            return

    def sigCheckPkg(self, po):
        """Verify the GPG signature of the given package object.

        :param po: the package object to verify the signature of
        :return: (result, error_string) 
           where result is::

              0 = GPG signature verifies ok or verification is not required.
              1 = GPG verification failed but installation of the right GPG key
                    might help.
              2 = Fatal GPG verification error, give up.
        """
        if self._override_sigchecks:
            check = False
            hasgpgkey = 0
        elif hasattr(po, 'pkgtype') and po.pkgtype == 'local':
            check = self.conf.localpkg_gpgcheck
            hasgpgkey = 0
        else:
            repo = self.repos.getRepo(po.repoid)
            check = repo.gpgcheck
            hasgpgkey = not not repo.gpgkey 
        
        if check:
            ts = self.rpmdb.readOnlyTS()
            sigresult = rpmUtils.miscutils.checkSig(ts, po.localPkg())
            localfn = os.path.basename(po.localPkg())
            
            if sigresult == 0:
                result = 0
                msg = ''

            elif sigresult == 1:
                if hasgpgkey:
                    result = 1
                else:
                    result = 2
                msg = _('Public key for %s is not installed') % localfn

            elif sigresult == 2:
                result = 2
                msg = _('Problem opening package %s') % localfn

            elif sigresult == 3:
                if hasgpgkey:
                    result = 1
                else:
                    result = 2
                result = 1
                msg = _('Public key for %s is not trusted') % localfn

            elif sigresult == 4:
                result = 2 
                msg = _('Package %s is not signed') % localfn
            
        else:
            result =0
            msg = ''

        return result, msg

    def cleanUsedHeadersPackages(self):
        """Delete the header and package files used in the
        transaction from the yum cache.
        """
        filelist = []
        for txmbr in self.tsInfo:
            if txmbr.po.state not in TS_INSTALL_STATES:
                continue
            if txmbr.po.repoid == "installed":
                continue
            if txmbr.po.repoid not in self.repos.repos:
                continue
            if txmbr.po.repo.keepcache:
                continue
            
            # make sure it's not a local file
            repo = self.repos.repos[txmbr.po.repoid]
            local = False
            for u in repo.baseurl:
                if u.startswith("file:"):
                    local = True
                    break
                
            if local:
                filelist.extend([txmbr.po.localHdr()])
            else:
                txmbr.po.xattr_origin_url # Load this, before we rm the file.
                filelist.extend([txmbr.po.localPkg(), txmbr.po.localHdr()])

        # now remove them
        for fn in filelist:
            if not os.path.exists(fn):
                continue
            try:
                misc.unlink_f(fn)
            except OSError, e:
                self.logger.warning(_('Cannot remove %s'), fn)
                continue
            else:
                self.verbose_logger.log(logginglevels.DEBUG_4,
                    _('%s removed'), fn)
        
    def cleanHeaders(self):
        """Delete the header files from the yum cache."""

        exts = ['hdr']
        return self._cleanFiles(exts, 'hdrdir', 'header')

    def cleanPackages(self):
        """Delete the package files from the yum cache."""

        exts = ['rpm']
        return self._cleanFiles(exts, 'pkgdir', 'package')

    def cleanSqlite(self):
        """Delete the sqlite files from the yum cache."""

        exts = ['sqlite', 'sqlite.bz2', 'sqlite.gz', 'sqlite.xz',
                'sqlite-journal']
        return self._cleanFiles(exts, 'cachedir', 'sqlite')

    def cleanMetadata(self):
        """Delete the metadata files from the yum cache."""

        exts = ['xml.gz', 'xml', 'cachecookie', 'mirrorlist.txt', 'asc',
                'xml.bz2', 'xml.xz']
        # Metalink is also here, but is a *.xml file
        return self._cleanFiles(exts, 'cachedir', 'metadata') 

    def cleanExpireCache(self):
        """Delete the local data saying when the metadata and mirror
           lists were downloaded for each repository."""

        exts = ['cachecookie', 'mirrorlist.txt']
        return self._cleanFiles(exts, 'cachedir', 'metadata')

    def cleanRpmDB(self):
        """Delete any cached data from the local rpmdb."""

        cachedir = self.conf.persistdir + "/rpmdb-indexes/"
        if not os.path.exists(cachedir):
            filelist = []
        else:
            filelist = misc.getFileList(cachedir, '', [])
        return self._cleanFilelist('rpmdb', filelist)

    def getCachedirGlob(self, dynvar):
        """Return a glob matching all dirs where yum stores cache files, based
        on cachedir and the given list of dynamic vars."""
        yumvar = self.conf.yumvar.copy()
        for d in dynvar:
            yumvar[d] = '*'
        instroot = config.varReplace(self.conf.installroot, self.conf.yumvar)
        cachedir = config.varReplace(self.conf._pristine_cachedir, yumvar)
        return (instroot + cachedir).replace('//', '/')

    def _cleanFiles(self, exts, pathattr, filetype):
        filelist = []
        for ext in exts:
            for repo in self.repos.listEnabled():
                path = getattr(repo, pathattr)
                if os.path.exists(path) and os.path.isdir(path):
                    filelist = misc.getFileList(path, ext, filelist)
        return self._cleanFilelist(filetype, filelist)

    def _cleanFilelist(self, filetype, filelist):
        removed = 0
        for item in filelist:
            try:
                misc.unlink_f(item)
            except OSError, e:
                self.logger.critical(_('Cannot remove %s file %s'), filetype, item)
                continue
            else:
                self.verbose_logger.log(logginglevels.DEBUG_4,
                    _('%s file %s removed'), filetype, item)
                removed+=1
        msg = P_('%d %s file removed', '%d %s files removed', removed) % (removed, filetype)
        return 0, [msg]

    def doPackageLists(self, pkgnarrow='all', patterns=None, showdups=None,
                       ignore_case=False, repoid=None):
        """Return a :class:`yum.misc.GenericHolder` containing
        lists of package objects.  The contents of the lists are
        specified in various ways by the arguments.

        :param pkgnarrow: a string specifying which types of packages
           lists to produces, such as updates, installed, available,
           etc.
        :param patterns: a list of names or wildcards specifying
           packages to list
        :param showdups: whether to include duplicate packages in the
           lists
        :param ignore_case: whether to ignore case when searching by
           package names
        :param repoid: repoid that all pkgs will belong to
        :return: a :class:`yum.misc.GenericHolder` instance with the
           following lists defined::

             available = list of packageObjects
             installed = list of packageObjects
             updates = tuples of packageObjects (updating, installed)
             extras = list of packageObjects
             obsoletes = tuples of packageObjects (obsoleting, installed)
             recent = list of packageObjects
        """
        if showdups is None:
            showdups = self.conf.showdupesfromrepos
        ygh = misc.GenericHolder(iter=pkgnarrow)
        
        installed = []
        available = []
        reinstall_available = []
        old_available = []
        updates = []
        obsoletes = []
        obsoletesTuples = []
        recent = []
        extras = []

        ic = ignore_case
        # list all packages - those installed and available, don't 'think about it'
        if pkgnarrow == 'all': 
            dinst = {}
            ndinst = {} # Newest versions by name.arch
            for po in self.rpmdb.returnPackages(patterns=patterns,
                                                ignore_case=ic):
                if not misc.filter_pkgs_repoid([po], repoid):
                    continue
                dinst[po.pkgtup] = po
                if showdups:
                    continue
                key = (po.name, po.arch)
                if key not in ndinst or po.verGT(ndinst[key]):
                    ndinst[key] = po
            installed = dinst.values()
                        
            if showdups:
                avail = self.pkgSack.returnPackages(repoid=repoid,
                                                    patterns=patterns,
                                                    ignore_case=ic)
            elif repoid:
                avail = self.pkgSack.sacks[repoid]
                avail = avail.returnNewestByNameArch(patterns=patterns,
                                                     ignore_case=ic)
            else:
                try:
                    avail = self.pkgSack.returnNewestByNameArch(patterns=patterns,
                                                              ignore_case=ic)
                except Errors.PackageSackError:
                    avail = []
            
            for pkg in avail:
                if showdups:
                    if pkg.pkgtup in dinst:
                        reinstall_available.append(pkg)
                    else:
                        available.append(pkg)
                else:
                    key = (pkg.name, pkg.arch)
                    if pkg.pkgtup in dinst:
                        reinstall_available.append(pkg)
                    else:
                        # if (self.allowedMultipleInstalls(pkg) or
                        #     key not in ndinst):
                        #  Might be because pattern specified a version, so
                        # we need to do a search for name/arch to find any
                        # installed. Alas. calling allowedMultipleInstalls()
                        # is much slower than calling searchNevra(). *Sigh*
                        ipkgs = self.rpmdb.searchNevra(pkg.name,
                                                       arch=pkg.arch)
                        ipkgs = misc.filter_pkgs_repoid(ipkgs, repoid)
                        if ipkgs:
                            ndinst[key] = sorted(ipkgs)[-1]

                        if key not in ndinst or pkg.verGT(ndinst[key]):
                            available.append(pkg)
                        else:
                            old_available.append(pkg)

        # produce the updates list of tuples
        elif pkgnarrow == 'updates':
            for (n,a,e,v,r) in self.up.getUpdatesList():
                matches = self.pkgSack.searchNevra(name=n, arch=a, epoch=e, 
                                                   ver=v, rel=r)
                # This is kind of wrong, depending on how you look at it.
                matches = misc.filter_pkgs_repoid(matches, repoid)
                if len(matches) > 1:
                    updates.append(matches[0])
                    self.verbose_logger.log(logginglevels.DEBUG_1,
                        _('More than one identical match in sack for %s'), 
                        matches[0])
                elif len(matches) == 1:
                    updates.append(matches[0])
                else:
                    self.verbose_logger.log(logginglevels.DEBUG_1,
                        _('Nothing matches %s.%s %s:%s-%s from update'), n,a,e,v,r)
            if patterns:
                exactmatch, matched, unmatched = \
                   parsePackages(updates, patterns, casematch=not ignore_case)
                updates = exactmatch + matched

        # installed only
        elif pkgnarrow == 'installed':
            installed = self.rpmdb.returnPackages(patterns=patterns,
                                                  ignore_case=ic)
            installed = misc.filter_pkgs_repoid(installed, repoid)
        
        # available in a repository
        elif pkgnarrow == 'available':

            if showdups:
                avail = self.pkgSack.returnPackages(patterns=patterns,
                                                    ignore_case=ic,
                                                    repoid=repoid)
            elif repoid:
                avail = self.pkgSack.sacks[repoid]
                avail = avail.returnNewestByNameArch(patterns=patterns,
                                                     ignore_case=ic)
            else:
                try:
                    avail = self.pkgSack.returnNewestByNameArch(patterns=patterns,
                                                              ignore_case=ic)
                except Errors.PackageSackError:
                    avail = []
            
            for pkg in avail:
                if showdups:
                    if self.rpmdb.contains(po=pkg):
                        reinstall_available.append(pkg)
                    else:
                        available.append(pkg)
                else:
                    ipkgs = self.rpmdb.searchNevra(pkg.name, arch=pkg.arch)
                    if ipkgs:
                        latest = sorted(ipkgs, reverse=True)[0]
                    if not ipkgs or pkg.verGT(latest):
                        available.append(pkg)
                    elif pkg.verEQ(latest):
                        reinstall_available.append(pkg)
                    else:
                        old_available.append(pkg)

        # not in a repo but installed
        elif pkgnarrow == 'extras':
            # we must compare the installed set versus the repo set
            # anything installed but not in a repo is an extra
            avail = self.pkgSack.simplePkgList(patterns=patterns,
                                               ignore_case=ic)
            avail = set(avail)
            for po in self.rpmdb.returnPackages(patterns=patterns,
                                                ignore_case=ic):
                if not misc.filter_pkgs_repoid([po], repoid):
                    continue
                if po.pkgtup not in avail:
                    extras.append(po)

        # not in a repo but installed
        elif pkgnarrow == 'distro-extras':
            for po in self.rpmdb.returnPackages(patterns=patterns,
                                                ignore_case=ic):
                if not misc.filter_pkgs_repoid([po], repoid):
                    continue
                if self.pkgSack.searchNames([po.name]):
                    continue
                extras.append(po)

        # obsoleting packages (and what they obsolete)
        elif pkgnarrow == 'obsoletes':
            self.conf.obsoletes = 1

            for (pkgtup, instTup) in self.up.getObsoletesTuples():
                (n,a,e,v,r) = pkgtup
                pkgs = self.pkgSack.searchNevra(name=n, arch=a, ver=v, rel=r, epoch=e)
                pkgs = misc.filter_pkgs_repoid(pkgs, repoid)
                instpo = self.getInstalledPackageObject(instTup)
                if len(pkgs) > 1:
                    self.verbose_logger.log(logginglevels.DEBUG_1,
                        _('More than one identical match in sack for %s'),
                        pkgs[0])
                if len(pkgs) >= 1:
                    obsoletes.append(pkgs[0])
                    obsoletesTuples.append((pkgs[0], instpo))
            if patterns:
                exactmatch, matched, unmatched = \
                   parsePackages(obsoletes, patterns, casematch=not ignore_case)
                obsoletes = exactmatch + matched
                matched_obsoletes = set(obsoletes)
                nobsoletesTuples = []
                for po, instpo in obsoletesTuples:
                    if po not in matched_obsoletes:
                        continue
                    nobsoletesTuples.append((po, instpo))
                obsoletesTuples = nobsoletesTuples
            if not showdups:
                obsoletes = packagesNewestByName(obsoletes)
                filt = set(obsoletes)
                nobsoletesTuples = []
                for po, instpo in obsoletesTuples:
                    if po not in filt:
                        continue
                    nobsoletesTuples.append((po, instpo))
                obsoletesTuples = nobsoletesTuples
        
        # packages recently added to the repositories
        elif pkgnarrow == 'recent':
            now = time.time()
            recentlimit = now-(self.conf.recent*86400)
            if showdups:
                avail = self.pkgSack.returnPackages(patterns=patterns,
                                                    ignore_case=ic,
                                                    repoid=repoid)
            elif repoid:
                avail = self.pkgSack.sacks[repoid]
                avail = avail.returnNewestByNameArch(patterns=patterns,
                                                     ignore_case=ic)
            else:
                try:
                    avail = self.pkgSack.returnNewestByNameArch(patterns=patterns,
                                                              ignore_case=ic)
                except Errors.PackageSackError:
                    avail = []
            
            for po in avail:
                if int(po.filetime) > recentlimit:
                    recent.append(po)
        
        
        ygh.installed = installed
        ygh.available = available
        ygh.reinstall_available = reinstall_available
        ygh.old_available = old_available
        ygh.updates = updates
        ygh.obsoletes = obsoletes
        ygh.obsoletesTuples = obsoletesTuples
        ygh.recent = recent
        ygh.extras = extras

        return ygh


        
    def findDeps(self, pkgs):
        """Return the dependencies for a given package object list, as well
        as possible solutions for those dependencies.
           
        :param pkgs: a list of package objects
        :return: the dependencies as a dictionary of dictionaries:
           packageobject = [reqs] = [list of satisfying pkgs]
        """
        results = {}

        for pkg in pkgs:
            results[pkg] = {} 
            reqs = pkg.requires
            reqs.sort()
            pkgresults = results[pkg] # shorthand so we don't have to do the
                                      # double bracket thing
            
            for req in reqs:
                (r,f,v) = req
                if r.startswith('rpmlib('):
                    continue
                
                satisfiers = []

                for po in self.whatProvides(r, f, v):
                    satisfiers.append(po)

                pkgresults[req] = satisfiers
        
        return results
    
    # pre 3.2.10 API used to always showdups, so that's the default atm.
    def searchGenerator(self, fields, criteria, showdups=True, keys=False, 
                                             searchtags=True, searchrpmdb=True):
        """Yield the packages that match the given search criteria.
        This generator method will lighten memory load for some
        searches, and is the preferred search function to use.

        :param fields: the fields to search
        :param criteria: a list of strings specifying the criteria to
           search for
        :param showdups: whether to yield duplicate packages from
           different repositories
        :param keys: setting *keys* to True will use the search keys
           that matched in the sorting, and return the search keys in
           the results
        :param searchtags: whether to search the package tags
        :param searchrpmdb: whether to search the rmpdb
           
        """
        sql_fields = []
        for f in fields:
            sql_fields.append(RPM_TO_SQLITE.get(f, f))

        # yield the results in order of most terms matched first
        sorted_lists = {} # count_of_matches = [(pkgobj, 
                          #                     [search strings which matched], 
                          #                     [results that matched])]
        tmpres = []
        real_crit = []
        real_crit_lower = [] # Take the s.lower()'s out of the loop
        rcl2c = {}
        # weigh terms in given order (earlier = more relevant)
        critweight = 0
        critweights = {}
        for s in criteria:
            real_crit.append(s)
            real_crit_lower.append(s.lower())
            rcl2c[s.lower()] = s
            critweights.setdefault(s, critweight)
            critweight -= 1

        for sack in self.pkgSack.sacks.values():
            tmpres.extend(sack.searchPrimaryFieldsMultipleStrings(sql_fields, real_crit))

        def results2sorted_lists(tmpres, sorted_lists):
            for (po, count) in tmpres:
                # check the pkg for sanity
                # pop it into the sorted lists
                tmpkeys   = set()
                tmpvalues = []
                if count not in sorted_lists: sorted_lists[count] = []
                for s in real_crit_lower:
                    for field in fields:
                        value = to_unicode(getattr(po, field))
                        if value and value.lower().find(s) != -1:
                            tmpvalues.append(value)
                            tmpkeys.add(rcl2c[s])

                if len(tmpvalues) > 0:
                    sorted_lists[count].append((po, tmpkeys, tmpvalues))

        results2sorted_lists(tmpres, sorted_lists)

        if searchrpmdb:
            tmpres = self.rpmdb.searchPrimaryFieldsMultipleStrings(fields,
                                                                   real_crit_lower,
                                                                   lowered=True)
            # close our rpmdb connection so we can ctrl-c, kthxbai
            self.closeRpmDB()

            results2sorted_lists(tmpres, sorted_lists)
            del tmpres

        results_by_pkg = {} # pkg=[list_of_tuples_of_values]
        if searchtags:
            tmpres = self.searchPackageTags(real_crit_lower)
            
            
            for pkg in tmpres:
                count = 0
                matchkeys = []
                tagresults = []
                for (match, taglist) in tmpres[pkg]:
                    count += len(taglist)
                    matchkeys.append(rcl2c[match])
                    tagresults.extend(taglist)
                    if pkg not in results_by_pkg:
                        results_by_pkg[pkg] = []
                    results_by_pkg[pkg].append((matchkeys, tagresults))

            del tmpres

        if sorted_lists.values():
            # do the ones we already have
            for item in sorted_lists.values():
                for pkg, k, v in item:
                    if pkg not in results_by_pkg:
                        results_by_pkg[pkg] = []
                    results_by_pkg[pkg].append((k,v))

        # take our existing dict-by-pkg and make the dict-by-count for 
        # this bizarro sorted_lists format
        # FIXME - stab sorted_lists in the chest at some later date
        sorted_lists = {}
        for pkg in results_by_pkg:
            totkeys = []
            totvals = []
            for (k, v) in results_by_pkg[pkg]:
                totkeys.extend(k)
                totvals.extend(v)
            
            totkeys = misc.unique(totkeys)
            totvals = misc.unique(totvals)
            count = len(totkeys)
            if count not in sorted_lists:
                sorted_lists[count] = []
            sorted_lists[count].append((pkg, totkeys, totvals))

        #  To explain why the following code looks like someone took drugs
        # before/during/after coding:
        #
        # We are sorting a list of: (po, tmpkeys, tmpvalues).
        #                  Eg.      (po, ['foo', 'bar'], ['matches foo',
        #                                                 'matches barx'])
        #
        # So we sort, and get a result like:
        #        po    | repo | matching value
        #     1. yum-1 |  fed | -2
        #     2. yum-2 |  fed | -2 
        #     3. yum-2 | @fed | -2
        #     4. yum-3 |  ups | -1
        # ...but without showdups we want to output _just_ #3, which requires
        # we find the newest EVR po for the best "matching value". Without keys
        # it's the same, except we just want the newest EVR.
        #  If we screw it up it's probably not even noticeable most of the time
        # either, so it's pretty thankless. HTH. HAND.
        # By default just sort using package sorting
        sort_func = operator.itemgetter(0)
        dup = lambda x: True
        if keys:
            # Take into account the keys found, their original order,
            # and number of fields hit as well
            sort_func = lambda x: (-sum((critweights[y] for y in x[1])),
                                   -len(x[2]), "\0".join(sorted(x[1])), x[0])
            dup = lambda x,y: sort_func(x)[:-1] == sort_func(y)[:-1]
        yielded = {}
        for val in reversed(sorted(sorted_lists)):
            last = None
            for sl_vals in sorted(sorted_lists[val], key=sort_func):
                if showdups:
                    (po, ks, vs) = sl_vals
                else:
                    if (sl_vals[0].name, sl_vals[0].arch) in yielded:
                        continue

                    na = (sl_vals[0].name, sl_vals[0].arch)
                    if last is None or (last[0] == na and dup(last[1],sl_vals)):
                        last = (na, sl_vals)
                        continue

                    (po, ks, vs) = last[1]
                    if last[0] == na: # Dito. yielded test above.
                        last = None
                    else:
                        last = (na, sl_vals)

                if keys:
                    yield (po, ks, vs)
                else:
                    yield (po, vs)

                if not showdups:
                    yielded[(po.name, po.arch)] = 1
            if last is not None:
                (po, ks, vs) = last[1]
                if keys:
                    yield (po, ks, vs)
                else:
                    yield (po, vs)

    def searchPackageTags(self, criteria):
        """Search for and return a list packages that have tags
        matching the given criteria.

        :param criteria: a list of strings specifying the criteria to
           search for
        :return: a list of package objects that have tags matching the
           given criteria
        """
        results = {} # name = [(criteria, taglist)]
        for c in criteria:
            c = c.lower()
            res = self.pkgtags.search_tags(c)
            for (name, taglist) in res.items():
                pkgs = self.pkgSack.searchNevra(name=name)
                if not pkgs:
                    continue
                pkg = pkgs[0]
                if pkg not in results:
                    results[pkg] = []
                results[pkg].append((c, taglist))
        
        return results
        
    def searchPackages(self, fields, criteria, callback=None):
        """Deprecated.  Search the specified fields for packages that
        match the given criteria, and return a list of the results.

        :param fields: the fields to search
        :param criteria: a list of strings specifying the criteria to
           search for
        :param callback: a function to print out the results as they
           are found.  *callback* should have the form callback(po,
           matched values list)
        """
        warnings.warn(_('searchPackages() will go away in a future version of Yum.\
                      Use searchGenerator() instead. \n'),
                Errors.YumFutureDeprecationWarning, stacklevel=2)           
        matches = {}
        match_gen = self.searchGenerator(fields, criteria)
        
        for (po, matched_strings) in match_gen:
            if callback:
                callback(po, matched_strings)
            if po not in matches:
                matches[po] = []
            
            matches[po].extend(matched_strings)
        
        return matches
    
    def searchPackageProvides(self, args, callback=None,
                              callback_has_matchfor=False):
        """Search for and return a list package objects that provide
        the given files or features.

        :param args: a list of strings specifying the files and
           features to search for the packages that provide
        :param callback: a callback function to print out the results
           as they are found
        :param callback_has_matchfor: whether the callback function
           will accept a list of strings to highlight in its output.
           If this is true, *args* will be passed to *callback* so
           that the files or features that were searched for can be
           highlighted
        """
        def _arg_data(arg):
            if not misc.re_glob(arg):
                isglob = False
                canBeFile = arg.startswith('/')
            else:
                isglob = True
                canBeFile = misc.re_filename(arg)

            return isglob, canBeFile

        matches = {}
        for arg in args:
            arg = to_unicode(arg)
            isglob, canBeFile = _arg_data(arg)

            if not isglob:
                usedDepString = True
                where = self.returnPackagesByDep(arg)
            else:
                usedDepString = False
                where = self.pkgSack.searchProvides(arg)
            self.verbose_logger.log(logginglevels.DEBUG_1,
               P_('Searching %d package', 'Searching %d packages', len(where)), len(where))
            
            for po in sorted(where):
                self.verbose_logger.log(logginglevels.DEBUG_2,
                    _('searching package %s'), po)
                tmpvalues = []
                
                if usedDepString:
                    tmpvalues.append(arg)

                if not isglob and canBeFile:
                    # then it is not a globbed file we have matched it precisely
                    tmpvalues.append(arg)
                    
                if isglob and canBeFile:
                    self.verbose_logger.log(logginglevels.DEBUG_2,
                        _('searching in file entries'))
                    for thisfile in po.dirlist + po.filelist + po.ghostlist:
                        if fnmatch.fnmatch(thisfile, arg):
                            tmpvalues.append(thisfile)
                

                self.verbose_logger.log(logginglevels.DEBUG_2,
                    _('searching in provides entries'))
                for (p_name, p_flag, (p_e, p_v, p_r)) in po.provides:
                    prov = misc.prco_tuple_to_string((p_name, p_flag, (p_e, p_v, p_r)))
                    if not usedDepString:
                        if fnmatch.fnmatch(p_name, arg) or fnmatch.fnmatch(prov, arg):
                            tmpvalues.append(prov)

                if len(tmpvalues) > 0:
                    if callback: # No matchfor, on globs
                        if not isglob and callback_has_matchfor:
                            callback(po, tmpvalues, args)
                        else:
                            callback(po, tmpvalues)
                    matches[po] = tmpvalues
        
        # installed rpms, too
        taglist = ['filelist', 'dirnames', 'provides_names']
        taglist_provonly = ['provides_names']
        for arg in args:
            isglob, canBeFile = _arg_data(arg)
            
            if not isglob:
                where = self.returnInstalledPackagesByDep(arg)
                usedDepString = True
                for po in where:
                    tmpvalues = [arg]

                    if len(tmpvalues) > 0:
                        if callback:
                            if callback_has_matchfor:
                                callback(po, tmpvalues, args)
                            else:
                                callback(po, tmpvalues)
                        matches[po] = tmpvalues

            else:
                usedDepString = False
                where = self.rpmdb

                if canBeFile:
                    arg_taglist = taglist
                else:
                    arg_taglist = taglist_provonly

                arg_regex = re.compile(fnmatch.translate(arg))
                for po in sorted(where):
                    searchlist = []
                    tmpvalues = []
                    for tag in arg_taglist:
                        tagdata = getattr(po, tag)
                        if tagdata is None:
                            continue
                        if type(tagdata) is types.ListType:
                            searchlist.extend(tagdata)
                        else:
                            searchlist.append(tagdata)
                    
                    for item in searchlist:
                        if arg_regex.match(item):
                            tmpvalues.append(item)
                
                    if len(tmpvalues) > 0:
                        if callback: # No matchfor, on globs
                            callback(po, tmpvalues)
                        matches[po] = tmpvalues
            
            
        return matches

    def _groupInstalledData(self, group):
        """ Return a dict of
             pkg_name =>
             (installed, available,
             backlisted-installed, blacklisted-available). """
        ret = {}
        if not group or self.conf.group_command != 'objects':
            return ret

        pkg_names = {}
        if group.groupid in self.igroups.groups:
            pkg_names = self.igroups.groups[group.groupid].pkg_names

        all_pkg_names = set(list(pkg_names))
        if hasattr(group, 'packages'): # If a comps. group, add remote pkgs.
            all_pkg_names.update(group.packages)

        for pkg_name in all_pkg_names:
            ipkgs = self.rpmdb.searchNames([pkg_name])
            if pkg_name not in pkg_names and not ipkgs:
                ret[pkg_name] = 'available'
                continue

            if not ipkgs:
                ret[pkg_name] = 'blacklisted-available'
                continue

            for ipkg in ipkgs:
                # Multiarch, if any are installed for the group we count "both"
                if ipkg.yumdb_info.get('group_member', '') != group.groupid:
                    continue
                ret[pkg_name] = 'installed'
                break
            else:
                ret[pkg_name] = 'blacklisted-installed'

        return ret

    def _groupInstalledEnvData(self, evgroup):
        """ Return a dict of
             grp_name =>
             (installed, available,
             backlisted-installed, blacklisted-available). """
        ret = {}
        if not evgroup or self.conf.group_command != 'objects':
            return ret

        grp_names = {}
        if evgroup.environmentid in self.igroups.groups:
            grp_names = self.igroups.environments[evgroup.environmentid]
            grp_names = grp_names.grp_names

        all_grp_names = set(list(grp_names))
        if hasattr(evgroup, 'allgroups'): # If a comps. evgroup, add remote grps
            all_grp_names.update(evgroup.allgroups)

        for grp_name in all_grp_names:
            igrp = self.igroups.groups.get(grp_name)
            if grp_name not in grp_names and not igrp:
                ret[grp_name] = 'available'
                continue

            if not igrp:
                ret[grp_name] = 'blacklisted-available'
                continue

            if igrp.environment == evgroup.environmentid:
                ret[grp_name] = 'installed'
            else:
                ret[grp_name] = 'blacklisted-installed'

        return ret

    def _groupReturnGroups(self, patterns=None, ignore_case=True):
        igrps = None
        ievgrps = None
        if patterns is None:
            grps = self.comps.groups
            if self.conf.group_command == 'objects':
                igrps = self.igroups.groups.values()
            evgrps = self.comps.environments
            if self.conf.group_command == 'objects':
                ievgrps = self.igroups.environments.values()
            return igrps, grps, ievgrps, evgrps

        gpats = []
        epats = []
        for pat in patterns:
            if pat.startswith('@^'):
                epats.append(pat[2:])
            elif pat.startswith('@'):
                gpats.append(pat[1:])
            else:
                epats.append(pat)
                gpats.append(pat)

        epats = ",".join(epats)
        gpats = ",".join(gpats)

        cs   = not ignore_case
        grps = self.comps.return_groups(gpats, case_sensitive=cs)
        #  Because we want name matches too, and we don't store group names
        # we need to add the groupid's we've found:
        if self.conf.group_command == 'objects':
            gpats = gpats + "," + ",".join([grp.groupid for grp in grps])
            igrps = self.igroups.return_groups(gpats, case_sensitive=cs)

        evgrps = self.comps.return_environments(epats, case_sensitive=cs)
        if self.conf.group_command == 'objects':
            epats = epats+ "," + ",".join([grp.environmentid for grp in evgrps])
            ievgrps = self.igroups.return_environments(epats, case_sensitive=cs)
        return igrps, grps, ievgrps, evgrps

    def doGroupLists(self, uservisible=0, patterns=None, ignore_case=True,
                     return_evgrps=False):
        """Return two lists of groups: installed groups and available
        groups.

        :param uservisible: If True, only groups marked as uservisible
           will be returned. Otherwise, all groups will be returned
        :param patterns: a list of stings.  If given, only groups
           with names that match the patterns will be included in the
           lists.  If not given, all groups will be included
        :param ignore_case: whether to ignore case when determining
           whether group names match the strings in *patterns*
        :param return_evgrps: whether to return environment groups as well as
           package groups
        """
        installed = []
        available = []
        einstalled = []
        eavailable = []

        if self.comps.compscount == 0:
            raise Errors.GroupsError, _('No group data available for configured repositories')
        
        igrps, grps, ievgrps, evgrps = self._groupReturnGroups(patterns,
                                                               ignore_case)

        if igrps is not None:
            digrps = {}
            for igrp in igrps:
                digrps[igrp.gid] = igrp
            igrps = digrps

        if ievgrps is not None:
            digrps = {}
            for ievgrp in ievgrps:
                digrps[ievgrp.evgid] = ievgrp
            ievgrps = digrps

        for grp in grps:
            if igrps is None:
                grp_installed = grp.installed
            else:
                grp_installed = grp.groupid in igrps
                if grp_installed:
                    del igrps[grp.groupid]

            if grp_installed:
                if uservisible:
                    if grp.user_visible:
                        installed.append(grp)
                else:
                    installed.append(grp)
            else:
                if uservisible:
                    if grp.user_visible:
                        available.append(grp)
                else:
                    available.append(grp)

        for evgrp in evgrps:
            if ievgrps is None:
                evgrp_installed = evgrp.installed
            else:
                evgrp_installed = evgrp.environmentid in ievgrps
                if evgrp_installed:
                    del ievgrps[evgrp.environmentid]

            if evgrp_installed:
                einstalled.append(evgrp)
            else:
                eavailable.append(evgrp)
            
        if igrps is None:
            igrps = {}
        if ievgrps is None:
            ievgrps = {}

        #  Note that we used to get here with igrps/ievgrps that didn't exist
        # in comps. but we mock them in comps now because it was hard to deal
        # with that everywhere ... so just to confirm.
        assert not igrps
        assert not ievgrps

        for igrp in igrps.values():
            #  These are installed groups that aren't in comps anymore. so we
            # create fake comps groups for them.
            grp = comps.Group()
            grp.groupid = igrp.gid
            grp.installed = True
            grp.name = grp.groupid
            for pkg_name in igrp.pkg_names:
                grp.mandatory_packages[pkg_name] = 1
            installed.append(grp)

        for ievgrp in ievgrps.values():
            #  These are installed evgroups that aren't in comps anymore. so we
            # create fake comps evgroups for them.
            evgrp = comps.Environment()
            grp.environmentid = ievgrp.evgid
            evgrp.installed = True
            evgrp.name = evgrp.environmentid
            evgrp._groups = list(ievgrp.groups)
            einstalled.append(evgrp)

        if return_evgrps:
            return (sorted(installed), sorted(available),
                    sorted(einstalled), sorted(eavailable))
        return sorted(installed), sorted(available)
    
    def groupRemove(self, grpid):
        """Mark all the packages in the given group to be removed.

        :param grpid: the name of the group containing the packages to
           mark for removal
        :return: a list of transaction members added to the
           transaction set by this function
        """
        txmbrs_used = []

        thesegroups = self.comps.return_groups(grpid)
        if not thesegroups:
            raise Errors.GroupsError, _("No Group named %s exists") % to_unicode(grpid)

        for thisgroup in thesegroups:
            igroup_data = self._groupInstalledData(thisgroup)

            thisgroup.toremove = True

            pkgs = thisgroup.packages
            gid  = thisgroup.groupid

            for pkg in pkgs:
                if pkg in igroup_data and igroup_data[pkg] != 'installed':
                    continue

                txmbrs = self.remove(name=pkg, silence_warnings=True)
                txmbrs_used.extend(txmbrs)
                for txmbr in txmbrs:
                    txmbr.groups.append(gid)
            if igroup_data:
                self.igroups.del_group(gid)
            
        return txmbrs_used

    def groupUnremove(self, grpid):
        """Unmark any packages in the given group from being removed.

        :param grpid: the name of the group to unmark the packages of
        """
        thesegroups = self.comps.return_groups(grpid)
        if not thesegroups:
            raise Errors.GroupsError, _("No Group named %s exists") % to_unicode(grpid)

        for thisgroup in thesegroups:
            thisgroup.toremove = False
            pkgs = thisgroup.packages
            for pkg in thisgroup.packages:
                for txmbr in self.tsInfo:
                    if txmbr.po.name == pkg and txmbr.po.state in TS_INSTALL_STATES:
                        try:
                            txmbr.groups.remove(grpid)
                        except ValueError:
                            self.verbose_logger.log(logginglevels.DEBUG_1,
                               _("package %s was not marked in group %s"), txmbr.po,
                                grpid)
                            continue
                        
                        # if there aren't any other groups mentioned then remove the pkg
                        if len(txmbr.groups) == 0:
                            self.tsInfo.remove(txmbr.po.pkgtup)
        
        
    def environmentRemove(self, evgrpid):
        """Mark all the packages in the given group to be removed.

        :param evgrpid: the name of the environment containing the groups to
           mark for removal
        :return: a list of transaction members added to the
           transaction set by this function
        """
        txmbrs_used = []

        thesegroups = self.comps.return_environments(evgrpid)
        if not thesegroups:
            raise Errors.GroupsError, _("No Environment named %s exists") % to_unicode(evgrpid)

        for thisgroup in thesegroups:
            igroup_data = self._groupInstalledEnvData(thisgroup)

            grps  = thisgroup.allgroups
            evgid = thisgroup.environmentid

            for grp in grps:
                if grp in igroup_data and igroup_data[grp] != 'installed':
                    continue

                txmbrs = self.groupRemove(grp)
                txmbrs_used.extend(txmbrs)
                for txmbr in txmbrs:
                    txmbr.environments.append(evgid)
            if igroup_data:
                self.igroups.del_environment(evgid)

        return txmbrs_used

    def selectGroup(self, grpid, group_package_types=[],
                    enable_group_conditionals=None, upgrade=False, ievgrp=None):
        """Mark all the packages in the given group to be installed.

        :param grpid: the name of the group containing the packages to
           mark for installation
        :param group_package_types: a list of the types of groups to
           work with.  This overrides self.conf.group_package_types
        :param enable_group_conditionals: overrides
           self.conf.enable_group_conditionals
        :return: a list of transaction members added to the
           transaction set by this function
        """

        txmbrs_used = []
        thesegroups = self.comps.return_groups(grpid)
     
        if not thesegroups:
            raise Errors.GroupsError, _("No Group named %s exists") % to_unicode(grpid)

        package_types = self.conf.group_package_types
        if group_package_types:
            package_types = group_package_types

        if self.conf.group_command == 'compat':
            upgrade = False

        for thisgroup in thesegroups:
            if thisgroup.selected:
                continue
            
            thisgroup.selected = True
            
            # Can move to upgrade, if installed and calling install
            lupgrade = upgrade

            pkgs = []
            if 'mandatory' in package_types:
                pkgs.extend(thisgroup.mandatory_packages)
            if 'default' in package_types:
                pkgs.extend(thisgroup.default_packages)
            if 'optional' in package_types:
                pkgs.extend(thisgroup.optional_packages)

            igroup_data = self._groupInstalledData(thisgroup)
            igrp = None
            if igroup_data:
                if thisgroup.groupid in self.igroups.groups:
                    igrp = self.igroups.groups[thisgroup.groupid]
                    lupgrade = True
                else:
                    self.igroups.add_group(thisgroup.groupid,
                                           thisgroup.packages, ievgrp)
            for pkg in igroup_data:
                if igroup_data[pkg] == 'installed':
                    pkgs.append(pkg)

            old_txmbrs = len(txmbrs_used)
            for pkg in pkgs:
                if self.conf.group_command == 'objects':
                    assert pkg in igroup_data
                    if (pkg not in igroup_data or
                        igroup_data[pkg].startswith('blacklisted')):
                        # (lupgrade and igroup_data[pkg] == 'available')):
                        msg = _('Skipping package %s from group %s')
                        self.verbose_logger.log(logginglevels.DEBUG_2,
                                                msg, pkg, thisgroup.groupid)
                        continue

                self.verbose_logger.log(logginglevels.DEBUG_2,
                    _('Adding package %s from group %s'), pkg, thisgroup.groupid)

                if igrp is not None:
                    igrp.pkg_names.add(pkg)
                    self.igroups.changed = True

                txmbrs = []
                try:
                    if (lupgrade and
                        (self.conf.group_command == 'simple' or
                         (igroup_data and igroup_data[pkg] == 'installed'))):
                        txmbrs = self.update(name = pkg,
                                             pkg_warning_level='debug2')
                    elif igroup_data and igroup_data[pkg] == 'installed':
                        pass # Don't upgrade on install.
                    else:
                        txmbrs = self.install(name = pkg,
                                              pkg_warning_level='debug2')
                        for txmbr in txmbrs:
                            txmbr.group_member = thisgroup.groupid
                            if lupgrade: # For list transaction.
                                txmbr._ugroup_member = thisgroup
                            else:
                                txmbr._igroup_member = thisgroup
                except Errors.InstallError, e:
                    self.verbose_logger.debug(_('No package named %s available to be installed'),
                        pkg)
                else:
                    txmbrs_used.extend(txmbrs)
                    for txmbr in txmbrs:
                        txmbr.groups.append(thisgroup.groupid)
            
            group_conditionals = self.conf.enable_group_conditionals
            if enable_group_conditionals is not None: # has to be this way so we can set it to False
                group_conditionals = enable_group_conditionals

            count_cond_test = 0
            # FIXME: What do we do about group conditionals when group==objects
            #        or group upgrade for group_command=simple?
            if not lupgrade and group_conditionals:
                for condreq, cond in thisgroup.conditional_packages.iteritems():
                    if self.isPackageInstalled(cond):
                        try:
                            txmbrs = self.install(name = condreq)
                        except Errors.InstallError:
                            # we don't care if the package doesn't exist
                            continue
                        else:
                            if cond not in self.tsInfo.conditionals:
                                self.tsInfo.conditionals[cond]=[]

                        txmbrs_used.extend(txmbrs)
                        for txmbr in txmbrs:
                            txmbr.groups.append(thisgroup.groupid)
                            self.tsInfo.conditionals[cond].append(txmbr.po)
                        continue
                    # Otherwise we hook into tsInfo.add to make sure
                    # we'll catch it if it's added later in this transaction
                    pkgs = self.pkgSack.searchNevra(name=condreq)
                    if pkgs:
                        if self.arch.multilib:
                            if self.conf.multilib_policy == 'best':
                                use = []
                                best = self.arch.legit_multi_arches
                                best.append('noarch')
                                for pkg in pkgs:
                                    if pkg.arch in best:
                                        use.append(pkg)
                                pkgs = use
                               
                        pkgs = packagesNewestByName(pkgs)
                        count_cond_test += len(pkgs)

                        if cond not in self.tsInfo.conditionals:
                            self.tsInfo.conditionals[cond] = []
                        self.tsInfo.conditionals[cond].extend(pkgs)

            if not lupgrade and len(txmbrs_used) == old_txmbrs:
                self.logger.critical(_('Warning: Group %s does not have any packages to install.'), thisgroup.groupid)
                if count_cond_test:
                    self.logger.critical(_('Group %s does have %u conditional packages, which may get installed.'),
                                         thisgroup.groupid, count_cond_test)
        return txmbrs_used

    def deselectGroup(self, grpid, force=False):
        """Unmark the packages in the given group from being
        installed.

        :param grpid: the name of the group containing the packages to
           unmark from installation
        :param force: if True, force remove all the packages in the
           given group from the transaction
        """
        
        if not self.comps.has_group(grpid):
            raise Errors.GroupsError, _("No Group named %s exists") % to_unicode(grpid)
            
        thesegroups = self.comps.return_groups(grpid)
        if not thesegroups:
            raise Errors.GroupsError, _("No Group named %s exists") % to_unicode(grpid)

        # FIXME: Do something with groups as objects, and env. groups.
        for thisgroup in thesegroups:
            thisgroup.selected = False
            
            for pkgname in thisgroup.packages:
                txmbrs = self.tsInfo.getMembersWithState(None,TS_INSTALL_STATES)
                for txmbr in txmbrs:
                    if txmbr.po.name != pkgname:
                        continue

                    if not force:
                        try: 
                            txmbr.groups.remove(grpid)
                        except ValueError:
                            self.verbose_logger.log(logginglevels.DEBUG_1,
                               _("package %s was not marked in group %s"), txmbr.po,
                                grpid)
                            continue
                        
                    # If the pkg isn't part of any group, or the group is
                    # being forced out ... then remove the pkg
                    if force or len(txmbr.groups) == 0:
                        self.tsInfo.remove(txmbr.po.pkgtup)
                        for pkg in self.tsInfo.conditionals.get(txmbr.name, []):
                            self.tsInfo.remove(pkg.pkgtup)
        
    def selectEnvironment(self, evgrpid, group_package_types=[],
                          enable_group_conditionals=None, upgrade=False):
        """Mark all the groups in the given environment group to be installed.

        :param evgrpid: the name of the env. group containing the groups to
           mark for installation
        :param group_package_types: a list of the types of groups to
           work with.  This overrides self.conf.group_package_types
        :param enable_group_conditionals: overrides
           self.conf.enable_group_conditionals
        :return: a list of transaction members added to the
           transaction set by this function
        """
        evgrps = self.comps.return_environments(evgrpid)
        if not evgrps:
            raise Errors.GroupsError, _("No Environment named %s exists") % to_unicode(evgrpid)

        ret = []
        for evgrp in evgrps:

            ievgrp = None
            if self.conf.group_command == 'compat':
                grps = ",".join(sorted(evgrp.groups))
            elif self.conf.group_command == 'simple':
                if not upgrade:
                    grps = ",".join(sorted(evgrp.groups))
                else: # Only upgrade the installed groups...
                    grps = []
                    for grpid in evgrp.groups:
                        grp = self.comps.return_group(grpid)
                        if grp is None:
                            continue
                        if not grp.installed:
                            continue
                        grps.append(grpid)
                    grps = ",".join(sorted(grps))
            elif self.conf.group_command == 'objects':
                igroup_data = self._groupInstalledEnvData(evgrp)
 
                grps = set()
                for grpid in evgrp.groups:
                    if (grpid not in igroup_data or
                        igroup_data[grpid].startswith('blacklisted')):
                        msg = _('Skipping group %s from environment %s')
                        self.verbose_logger.log(logginglevels.DEBUG_2,
                                                msg, grpid, evgrp.environmentid)
                        continue
                    grps.add(grpid)
                if evgrp.environmentid in self.igroups.environments:
                    ievgrp = self.igroups.environments[evgrp.environmentid]
                    #  Add groups from the installed evgrp, for Eg. installed
                    # only evgrps.
                    for grp_name in ievgrp.grp_names:
                        if grp_name not in self.igroups.groups:
                            continue
                        grp_evgrpid = self.igroups.groups[grp_name].environment
                        if grp_evgrpid != evgrp.environmentid:
                            continue
                        grps.add(grp_name)
                else:
                    ievgrp = self.igroups.add_environment(evgrp.environmentid,
                                                          evgrp.allgroups)
                grps = ",".join(sorted(grps))

            try:
                txs = self.selectGroup(grps,
                                       group_package_types,
                                       enable_group_conditionals, upgrade,
                                       ievgrp=ievgrp)
            except Errors.GroupsError:
                continue
            ret.extend(txs)
        return ret

    def deselectEnvironment(self, evgrpid, force=False):
        """Unmark the groups in the given environment group from being
        installed.

        :param evgrpid: the name of the environment group containing the
           groups to unmark from installation
        :param force: if True, force remove all the packages in the
           given groups from the transaction
        """
        evgrps = self.comps.return_environments(evgrpid)
        if not thesegroups:
            raise Errors.GroupsError, _("No Environment named %s exists") % to_unicode(evgrpid)

        for evgrp in evgrps:
            grps = ",".join(sorted(evgrp.groups))
            self.deselectGroup(grps, force)
            # FIXME: env. needs to be marked not-to-be-installed, etc.

    def getPackageObject(self, pkgtup, allow_missing=False):
        """Return a package object that corresponds to the given
        package tuple.

        :param pkgtup: the package tuple specifying the package object
           to return

        :param allow_missing: If no package corresponding to the given
           package tuple can be found, None is returned if
           *allow_missing* is True, and a :class:`yum.Errors.DepError` is
           raised if *allow_missing* is False.
        :return: a package object corresponding to the given package tuple
        :raises: a :class:`yum.Errors.DepError` if no package
           corresponding to the given package tuple can be found, and
           *allow_missing* is False
        """
        # look it up in the self.localPackages first:
        for po in self.localPackages:
            if po.pkgtup == pkgtup:
                return po
                
        pkgs = self.pkgSack.searchPkgTuple(pkgtup)

        if len(pkgs) == 0:
            self._add_not_found_a(pkgs, pkgtup=pkgtup)
            if allow_missing: #  This can happen due to excludes after .up has
                return None   # happened.
            raise Errors.DepError, _('Package tuple %s could not be found in packagesack') % str(pkgtup)
            
        if len(pkgs) > 1: # boy it'd be nice to do something smarter here FIXME
            result = pkgs[0]
        else:
            result = pkgs[0] # which should be the only
        
            # this is where we could do something to figure out which repository
            # is the best one to pull from
        
        return result

    def getInstalledPackageObject(self, pkgtup):
        """Return a :class:`yum.packages.YumInstalledPackage` object that
        corresponds to the given package tuple.  This function should
        be used instead of :func:`searchPkgTuple` if you are assuming
        that the package object exists.

        :param pkgtup: the package tuple specifying the package object
           to return
        :return: a :class:`yum.packages.YumInstalledPackage` object corresponding
           to the given package tuple
        :raises: a :class:`yum.Errors.RpmDBError` if the specified package
           object cannot be found
        """
        pkgs = self.rpmdb.searchPkgTuple(pkgtup)
        if len(pkgs) == 0:
            self._add_not_found_i(pkgs, pkgtup=pkgtup)
            raise Errors.RpmDBError, _('Package tuple %s could not be found in rpmdb') % str(pkgtup)

        # Dito. FIXME from getPackageObject() for len() > 1 ... :)
        po = pkgs[0] # take the first one
        return po
        
    def gpgKeyCheck(self):
        """Checks for the presence of GPG keys in the rpmdb.

        :return: 0 if there are no GPG keys in the rpmdb, and 1 if
           there are keys
        """
        gpgkeyschecked = self.conf.cachedir + '/.gpgkeyschecked.yum'
        if os.path.exists(gpgkeyschecked):
            return 1
            
        myts = rpmUtils.transaction.initReadOnlyTransaction(root=self.conf.installroot)
        myts.pushVSFlags(~(rpm._RPMVSF_NOSIGNATURES|rpm._RPMVSF_NODIGESTS))
        idx = myts.dbMatch('name', 'gpg-pubkey')
        keys = idx.count()
        del idx
        del myts
        
        if keys == 0:
            return 0
        else:
            mydir = os.path.dirname(gpgkeyschecked)
            if not os.path.exists(mydir):
                os.makedirs(mydir)
                
            fo = open(gpgkeyschecked, 'w')
            fo.close()
            del fo
            return 1

    def returnPackagesByDep(self, depstring):
        """Return a list of package objects that provide the given
        dependencies. 

        :param depstring: a string specifying the dependency to return
           the packages that fulfil
        :return: a list of packages that fulfil the given dependency
        """
        if not depstring:
            return []

        # parse the string out
        #  either it is 'dep (some operator) e:v-r'
        #  or /file/dep
        #  or packagename
        if type(depstring) == types.TupleType:
            (depname, depflags, depver) = depstring
        else:
            depname = depstring
            depflags = None
            depver = None

            if depstring[0] != '/':
                # not a file dep - look at it for being versioned
                dep_split = depstring.split()
                if len(dep_split) == 3:
                    depname, flagsymbol, depver = dep_split
                    if not flagsymbol in SYMBOLFLAGS:
                        raise Errors.YumBaseError, _('Invalid version flag from: %s') % str(depstring)
                    depflags = SYMBOLFLAGS[flagsymbol]

        if depflags is None: # This does wildcards...
            return self.pkgSack.searchProvides(depstring)

        # This does flags+versions, but no wildcards...
        return self.pkgSack.getProvides(depname, depflags, depver).keys()

    def returnPackageByDep(self, depstring):
        """Return the best, or first, package object that provides the
        given dependencies.

        :param depstring: a string specifying the dependency to return
           the package that fulfils
        :return: the best, or first, package that fulfils the given
           dependency
        :raises: a :class:`yum.Errors.YumBaseError` if no packages that
           fulfil the given dependency can be found
        """
        # we get all sorts of randomness here
        errstring = depstring
        if type(depstring) not in types.StringTypes:
            errstring = str(depstring)
        
        try:
            pkglist = self.returnPackagesByDep(depstring)
        except Errors.YumBaseError:
            raise Errors.YumBaseError, _('No Package found for %s') % errstring
        
        ps = ListPackageSack(pkglist)
        result = self._bestPackageFromList(ps.returnNewestByNameArch(),
                                           req=errstring)
        if result is None:
            raise Errors.YumBaseError, _('No Package found for %s') % errstring
        
        return result

    def returnInstalledPackagesByDep(self, depstring):
        """Return a list of installed package objects that provide the
        given dependencies.

        :param depstring: a string specifying the dependency to return
           the packages that fulfil
        :return: a list of installed packages that fulfil the given
           dependency
        """
        if not depstring:
            return []

        # parse the string out
        #  either it is 'dep (some operator) e:v-r'
        #  or /file/dep
        #  or packagename
        if type(depstring) == types.TupleType:
            (depname, depflags, depver) = depstring
        else:
            depname = depstring
            depflags = None
            depver = None
            
            if depstring[0] != '/':
                # not a file dep - look at it for being versioned
                dep_split = depstring.split()
                if len(dep_split) == 3:
                    depname, flagsymbol, depver = dep_split
                    if not flagsymbol in SYMBOLFLAGS:
                        raise Errors.YumBaseError, _('Invalid version flag from: %s') % str(depstring)
                    depflags = SYMBOLFLAGS[flagsymbol]

        if depflags is None: # This does wildcards...
            return self.rpmdb.searchProvides(depstring)

        # This does flags+versions, but no wildcards...
        return self.rpmdb.getProvides(depname, depflags, depver).keys()

    def returnInstalledPackageByDep(self, depstring):
        """Return the best, or first, installed package object that provides the
        given dependencies.

        :param depstring: a string specifying the dependency to return
           the package that fulfils
        :return: the best, or first, installed package that fulfils the given
           dependency
        :raises: a :class:`yum.Errors.YumBaseError` if no packages that
           fulfil the given dependency can be found
        """
        # we get all sorts of randomness here
        errstring = depstring
        if type(depstring) not in types.StringTypes:
            errstring = str(depstring)
        
        try:
            pkglist = self.returnInstalledPackagesByDep(depstring)
        except Errors.YumBaseError:
            raise Errors.YumBaseError, _('No Package found for %s') % errstring
        
        ps = ListPackageSack(pkglist)
        result = self._bestPackageFromList(ps.returnNewestByNameArch(),
                                           req=errstring)
        if result is None:
            raise Errors.YumBaseError, _('No Package found for %s') % errstring
        
        return result

    def _bestPackageFromList(self, pkglist, req=None):
        """take list of package objects and return the best package object.
           If the list is empty, return None. 
           
           Note: this is not aware of multilib so make sure you're only
           passing it packages of a single arch group.

           :param pkglist: the list of packages to return the best
             packages from
           :param req: the requirement from the user
           :return: a list of the best packages from *pkglist*
        """
        
        
        if len(pkglist) == 0:
            return None
            
        if len(pkglist) == 1:
            return pkglist[0]

        bestlist = self._compare_providers(pkglist, reqpo=None, req=req)
        return bestlist[0][0]

    def bestPackagesFromList(self, pkglist, arch=None, single_name=False,
                             req=None):
        """Return the best packages from a list of packages.  This
        function is multilib aware, so that it will not compare
        multilib to singlelib packages.

        :param pkglist: the list of packages to return the best
           packages from
        :param arch: packages will be selected that are compatible
           with the architecture specified by *arch*
        :param single_name: whether to return a single package name
        :param req: the requirement from the user
        :return: a list of the best packages from *pkglist*
        """
        returnlist = []
        compatArchList = self.arch.get_arch_list(arch)
        multiLib = []
        singleLib = []
        noarch = []
        for po in pkglist:
            if po.arch not in compatArchList:
                continue
            elif po.arch in ("noarch"):
                noarch.append(po)
            elif isMultiLibArch(arch=po.arch):
                multiLib.append(po)
            else:
                singleLib.append(po)
                
        # we now have three lists.  find the best package(s) of each
        multi = self._bestPackageFromList(multiLib, req=req)
        single = self._bestPackageFromList(singleLib, req=req)
        no = self._bestPackageFromList(noarch, req=req)

        if single_name and multi and single and multi.name != single.name:
            # Sinlge _must_ match multi, if we want a single package name
            single = None

        # now, to figure out which arches we actually want
        # if there aren't noarch packages, it's easy. multi + single
        if no is None:
            if multi: returnlist.append(multi)
            if single: returnlist.append(single)
        # if there's a noarch and it's newer than the multilib, we want
        # just the noarch.  otherwise, we want multi + single
        elif multi:
            best = self._bestPackageFromList([multi,no], req=req)
            if best.arch == "noarch":
                returnlist.append(no)
            else:
                if multi: returnlist.append(multi)
                if single: returnlist.append(single)
        # similar for the non-multilib case
        elif single:
            best = self._bestPackageFromList([single,no], req=req)
            if best.arch == "noarch":
                returnlist.append(no)
            else:
                returnlist.append(single)
        # if there's not a multi or single lib, then we want the noarch
        else:
            returnlist.append(no)

        return returnlist

    # FIXME: This doesn't really work, as it assumes one obsoleter for each pkg
    # when we can have:
    # 1 pkg obsoleted by multiple pkgs _and_
    # 1 pkg obsoleting multiple pkgs
    # ...and we need to detect loops, and get the arches "right" and do this
    # for chains. Atm. I hate obsoletes, and I can't get it to work better,
    # easily ... so screw it, don't create huge chains of obsoletes with some
    # loops in there too ... or I'll have to hurt you.
    def _pkg2obspkg(self, po):
        """ Given a package return the package it's obsoleted by and so
            we should install instead. Or None if there isn't one. """
        if self._up is not None:
            thispkgobsdict = self.up.checkForObsolete([po.pkgtup])
        else:
            #  This is pretty hacky, but saves a huge amount of time for small
            # ops.
            if not self.conf.obsoletes:
                return None

            if not hasattr(self, '_up_obs_hack'):
                obs_init = time.time()
                up = rpmUtils.updates.Updates([], [])
                up.rawobsoletes = self.pkgSack.returnObsoletes(newest=True)
                self.verbose_logger.debug('Obs Init time: %0.3f' % (time.time()
                                                                    - obs_init))
                self._up_obs_hack = up
            thispkgobsdict = self._up_obs_hack.checkForObsolete([po.pkgtup])

        if po.pkgtup in thispkgobsdict:
            obsoleting  = thispkgobsdict[po.pkgtup]
            oobsoleting = []
            # We want to keep the arch. of the obsoleted pkg. if possible.
            for opkgtup in obsoleting:
                if not canCoinstall(po.arch, opkgtup[1]):
                    oobsoleting.append(opkgtup)
            if oobsoleting:
                obsoleting = oobsoleting
            if len(obsoleting) > 1:
                # Pick the first name, and run with it...
                first = obsoleting[0]
                obsoleting = [pkgtup for pkgtup in obsoleting
                              if first[0] == pkgtup[0]]
            if len(obsoleting) > 1:
                # Lock to the latest version...
                def _sort_ver(x, y):
                    n1,a1,e1,v1,r1 = x
                    n2,a2,e2,v2,r2 = y
                    return compareEVR((e1,v1,r1), (e2,v2,r2))
                obsoleting.sort(_sort_ver)
                first = obsoleting[0]
                obsoleting = [pkgtup for pkgtup in obsoleting
                              if not _sort_ver(first, pkgtup)]
            if len(obsoleting) > 1:
                # Now do arch distance (see depsolve:compare_providers)...
                def _sort_arch_i(carch, a1, a2):
                    res1 = archDifference(carch, a1)
                    if not res1:
                        return 0
                    res2 = archDifference(carch, a2)
                    if not res2:
                        return 0
                    return res1 - res2
                def _sort_arch(x, y):
                    n1,a1,e1,v1,r1 = x
                    n2,a2,e2,v2,r2 = y
                    ret = _sort_arch_i(po.arch,            a1, a2)
                    if ret:
                        return ret
                    ret = _sort_arch_i(self.arch.bestarch, a1, a2)
                    return ret
                obsoleting.sort(_sort_arch)
            for pkgtup in obsoleting:
                pkg = self.getPackageObject(pkgtup, allow_missing=True)
                if pkg is not None:
                    return pkg
            return None
        return None

    def _test_loop(self, node, next_func):
        """ Generic comp. sci. test for looping, walk the list with two pointers
            moving one twice as fast as the other. If they are ever == you have
            a loop. If loop we return None, if no loop the last element. """
        slow = node
        done = False
        while True:
            next = next_func(node)
            if next is None and not done: return None
            if next is None: return node
            node = next_func(next)
            if node is None: return next
            done = True

            slow = next_func(slow)
            if node == slow:
                return None

    def _try_bold(self, string_):
        """Attempt to make the string look bold in terminal."""
        if hasattr(self, 'term'):
            return '%s%s%s' % (self.term.MODE['bold'], string_, self.term.MODE['normal'])
        return string_

    def _at_groupinstall(self, pattern, upgrade=False):
        " Do groupinstall via. leading @ on the cmd line, for install."
        assert pattern[0] == '@'
        group_string = pattern[1:]
        tx_return = []

        try: comps = self.comps
        except yum.Errors.GroupsError, e:
            # No Groups Available in any repository?
            # This also means no installed groups, when using objects.
            self.logger.warning(e)
            return tx_return

        found = False
        if group_string and group_string[0] == '^':
            group_string = group_string[1:]
            # Actually dealing with "environment groups".
            for env_grp in comps.return_environments(group_string):
                found = True
                txmbrs = self.selectEnvironment(env_grp.environmentid,
                                                upgrade=upgrade)
                tx_return.extend(txmbrs)
        else:
            for group in comps.return_groups(group_string):
                found = True
                txmbrs = self.selectGroup(group.groupid, upgrade=upgrade)
                tx_return.extend(txmbrs)
        if not found:
            raise Errors.GroupInstallError, _('Group %s does not exist.') % self._try_bold(group_string)
        return tx_return

    def _at_groupupgrade(self, pattern):
        " Do group upgrade via. leading @ on the cmd line, for update."
        try:
            return self._at_groupinstall(pattern, upgrade=True)
        except Errors.GroupInstallError, e:
            self.logger.warning(_('Warning: %s'), e)
            if self.conf.skip_missing_names_on_update:
                return []
            else:
                raise

    def _at_groupremove(self, pattern):
        " Do groupremove via. leading @ on the cmd line, for remove."
        assert pattern[0] == '@'
        group_string = pattern[1:]
        tx_return = []

        if group_string and group_string[0] == '^':
            group_string = group_string[1:]
            # Actually dealing with "environment groups".
            try:
                txmbrs = self.environmentRemove(group_string)
            except yum.Errors.GroupsError:
                self.logger.critical(_('Warning: Environment Group %s does not exist.'), group_string)
            else:
                tx_return.extend(txmbrs)
            return tx_return

        try:
            txmbrs = self.groupRemove(group_string)
        except yum.Errors.GroupsError:
            self.logger.critical(_('No group named %s exists'), group_string)
        else:
            tx_return.extend(txmbrs)
        return tx_return

    #  Note that this returns available pkgs, and not txmbrs like the other
    # _at_group* functions.
    def _at_groupdowngrade(self, pattern):
        " Do downgrade of a group via. leading @ on the cmd line."
        assert pattern[0] == '@'
        grpid = pattern[1:]

        # FIXME: **** environment groups and groups as objects... ****

        thesegroups = self.comps.return_groups(grpid)
        if not thesegroups:
            raise Errors.GroupsError, _("No Group named %s exists") % to_unicode(grpid)
        pkgnames = set()
        for thisgroup in thesegroups:
            pkgnames.update(thisgroup.packages)
        return self.pkgSack.searchNames(pkgnames)

    def _minus_deselect(self, pattern):
        """ Remove things from the transaction, like kickstart. """
        assert pattern[0] == '-'
        pat = pattern[1:].strip()

        if pat and pat.startswith('@^'):
            pat = pat[2:]
            return self.deselectEnvironment(pat)

        if pat and pat[0] == '@':
            pat = pat[1:]
            return self.deselectGroup(pat)

        return self.tsInfo.deselect(pat)

    def _find_obsoletees(self, po):
        """ Return the pkgs. that are obsoleted by the po we pass in. """
        if not self.conf.obsoletes:
            return

        if not isinstance(po, YumLocalPackage):
            for (obstup, inst_tup) in self.up.getObsoletersTuples(name=po.name):
                if po.pkgtup == obstup:
                    installed_pkg =  self.getInstalledPackageObject(inst_tup)
                    yield installed_pkg
        else:
            for pkg in self._find_obsoletees_direct(po):
                yield pkg

    def _find_obsoletees_direct(self, po):
        """ Return the pkgs. that are obsoleted by the po we pass in. This works
            directly on the package data, for two reasons:
            1. Consulting .up. has a slow setup for small/fast ops.
            2. We need this work even if obsoletes are turned off, because rpm
               will be doing it for us. """
        for obs_n in po.obsoletes_names:
            for pkg in self.rpmdb.searchNevra(name=obs_n):
                if pkg.obsoletedBy([po]):
                    yield pkg

    def _add_prob_flags(self, *flags):
        """ Add all of the passed flags to the tsInfo.probFilterFlags array. """
        for flag in flags:
            if flag not in self.tsInfo.probFilterFlags:
                self.tsInfo.probFilterFlags.append(flag)

    def _enable_oldpackage_flag(self, po):
        """Add RPMPROB_FILTER_OLDPACKAGE if the package requires it."""
        for ipkg in self.rpmdb.searchNevra(name=po.name):
            if ipkg.verGT(po) and not canCoinstall(ipkg.arch, po.arch):
                self._add_prob_flags(rpm.RPMPROB_FILTER_OLDPACKAGE)
                return True
        return False

    def _install_is_upgrade(self, po, ipkgs):
        """ See if po is an upgradeable version of an installed pkg.
        Non-compat. arch differences mean no. """

        if False and self._up is not None:
            #  This is the old code, not sure it's good to have two paths. And
            # we don't want to create .up. (which requires init repos.) if we
            # don't have to.
            return po.pkgtup in self.up.updating_dict

        if self.allowedMultipleInstalls(po):
            return False

        for ipkg in ipkgs:
            if po.verLE(ipkg):
                continue
            if po.arch == ipkg.arch: # always fine.
                return True
            if 'noarch' in (po.arch, ipkg.arch):
                return True
            if not self.arch.multilib:
                return True
            if canCoinstall(po.arch, ipkg.arch):
                continue
            return True

        return False

    def _valid_install_arch(self, po, ipkgs=None):
        ''' See if we can install this arch of package, mainly for
            i386 vs. i586 or ppc64 vs. ppc64 etc. '''
        if not ipkgs:
            ipkgs = self.rpmdb.searchNames([po.name])
        else:
            ipkgs = ipkgs[:]

        # Add these anyway, just to be sure.
        for txmbr in self.tsInfo.matchNaevr(po.name):
            if txmbr.output_state not in TS_INSTALL_STATES:
                continue
            ipkgs.append(txmbr.po)

        for ipkg in ipkgs:
            if po.arch == ipkg.arch:
                continue
            if not po.verEQ(ipkg):
                continue
            if canCoinstall(po.arch, ipkg.arch):
                continue
            self.verbose_logger.log(logginglevels.INFO_2,
                    _("Package: %s - can't co-install with %s"), po, ipkg)
            return False
        return True

    def _valid_obsoleter_arch(self, obsoleter, obsoletee):
        """Return whether this obsoleter meets multilib_policy in case we are
        dealing with the noarch->arch obsoletion case."""
        if not self.arch.multilib or self.conf.multilib_policy != 'best':
            # Install everything
            return True
        if obsoletee.arch != 'noarch' or obsoleter.arch == 'noarch':
            # We do respect any arch->(no)arch obsoletions (having
            # obsoletee.i386 installed on x86_64, you'd still expect
            # obsoleter.i386 to replace it, even if you have
            # multilib_policy=best).
            return True
        # noarch->arch case
        return obsoleter.arch in self.arch.legit_multi_arches

    def install(self, po=None, **kwargs):
        """Mark the specified item for installation.  If a package
        object is given, mark it for installation.  Otherwise, mark
        the best package specified by the key word arguments for
        installation.

        :param po: a package object to install
        :param kwargs: if *po* is not specified, these keyword
           arguments will be used to find the best package to install
        :return: a list of the transaction members added to the
           transaction set by this function
        :raises: :class:`yum.Errors.InstallError` if there is a problem
           installing the package
        """
        

        #  This is kind of hacky, we really need a better way to do errors than
        # doing them directly from .install/etc. ... but this is easy. *sigh*.
        #  We are only using this in "groupinstall" atm. ... so we don't have
        # a long list of "blah already installed." messages when people run
        # "groupinstall mygroup" in yum-cron etc.
        pkg_warn = kwargs.get('pkg_warning_level', 'flibble')
        def _dbg2(*args, **kwargs):
            self.verbose_logger.log(logginglevels.DEBUG_2, *args, **kwargs)
        level2func = {'debug2' : _dbg2,
                      'warning' : self.verbose_logger.warning}
        if pkg_warn not in level2func:
            pkg_warn = 'warning'
        pkg_warn = level2func[pkg_warn]

        pkgs = []
        was_pattern = False
        if po:
            if isinstance(po, YumAvailablePackage) or isinstance(po, YumLocalPackage):
                pkgs.append(po)
            else:
                raise Errors.InstallError, _('Package Object was not a package object instance')
            
        else:
            if not kwargs:
                raise Errors.InstallError, _('Nothing specified to install')

            if 'pattern' in kwargs:
                if kwargs['pattern'] and kwargs['pattern'][0] == '-':
                    return self._minus_deselect(kwargs['pattern'])

                if kwargs['pattern'] and kwargs['pattern'][0] == '@':
                    return self._at_groupinstall(kwargs['pattern'])

                repoid = None # All of them
                if 'repoid' in kwargs:
                    repoid = kwargs['repoid']

                was_pattern = True
                pats = [kwargs['pattern']]
                mypkgs = self.pkgSack.returnPackages(patterns=pats,
                                                     repoid=repoid,
                                                      ignore_case=False)
                pkgs.extend(mypkgs)
                # if we have anything left unmatched, let's take a look for it
                # being a dep like glibc.so.2 or /foo/bar/baz
                
                if not mypkgs:
                    arg = kwargs['pattern']
                    self.verbose_logger.debug(_('Checking for virtual provide or file-provide for %s'), 
                        arg)

                    mypkgs = self.returnPackagesByDep(arg)
                    if repoid:
                        mypkgs = misc.filter_pkgs_repoid(mypkgs, repoid)
                    if not misc.re_glob(arg):
                        mypkgs = self.bestPackagesFromList(mypkgs,
                                                           single_name=True,
                                                           req=arg)
                    pkgs.extend(mypkgs)
            else:
                nevra_dict = self._nevra_kwarg_parse(kwargs)

                pkgs = self.pkgSack.searchNevra(name=nevra_dict['name'],
                     epoch=nevra_dict['epoch'], arch=nevra_dict['arch'],
                     ver=nevra_dict['version'], rel=nevra_dict['release'])
                self._add_not_found_a(pkgs, nevra_dict)
                
                pkgs = misc.filter_pkgs_repoid(pkgs, kwargs.get('repoid'))

            if pkgs:
                # if was_pattern or nevra-dict['arch'] is none, take the list
                # of arches based on our multilib_compat config and 
                # toss out any pkgs of any arch NOT in that arch list

                
                # only do these things if we're multilib
                if self.arch.multilib:
                    if was_pattern or not nevra_dict['arch']: # and only if they
                                                              # they didn't specify an arch
                        if self.conf.multilib_policy == 'best':
                            pkgs_by_name = {}
                            use = []
                            not_added = []
                            best = self.arch.legit_multi_arches
                            best.append('noarch')
                            for pkg in pkgs:
                                if pkg.arch in best:
                                    pkgs_by_name[pkg.name] = 1    
                                    use.append(pkg)  
                                else:
                                    not_added.append(pkg)
                            for pkg in not_added:
                                if not pkg.name in pkgs_by_name:
                                    use.append(pkg)
                           
                            pkgs = use
                           
                pkgs = packagesNewestByName(pkgs)

                pkgbyname = {}
                for pkg in pkgs:
                    if pkg.name not in pkgbyname:
                        pkgbyname[pkg.name] = [ pkg ]
                    else:
                        pkgbyname[pkg.name].append(pkg)

                lst = []
                for pkgs in pkgbyname.values():
                    lst.extend(self.bestPackagesFromList(pkgs))
                pkgs = lst


        if not pkgs:
            # Do we still want to return errors here?
            # We don't in the cases below, so I didn't here...
            if 'pattern' in kwargs:
                arg = kwargs['pattern']
                pkgs = self.rpmdb.returnPackages(patterns=[arg],
                                                 ignore_case=False)
                if not pkgs:
                    self.verbose_logger.debug(
                        _('Checking for installed virtual provide or file-provide for %s'),
                        arg)
                    pkgs = self.returnInstalledPackagesByDep(arg)
            if 'name' in kwargs:
                pkgs = self.rpmdb.searchNevra(name=kwargs['name'])
            if 'pkgtup' in kwargs:
                pkgs = self.rpmdb.searchNevra(name=kwargs['pkgtup'][0])
            # Warning here does "weird" things when doing:
            # yum --disablerepo='*' install '*'
            # etc. ... see RHBZ#480402
            if False:
                for pkg in pkgs:
                    self.verbose_logger.warning(_('Package %s installed and not available'), pkg)
            if pkgs:
                return []
            raise Errors.InstallError, _('No package(s) available to install')
        
        # FIXME - lots more checking here
        #  - install instead of erase
        #  - better error handling/reporting


        tx_return = []
        for po in pkgs:
            if self.tsInfo.exists(pkgtup=po.pkgtup):
                if self.tsInfo.getMembersWithState(po.pkgtup, TS_INSTALL_STATES):
                    self.verbose_logger.log(logginglevels.DEBUG_1,
                        _('Package: %s  - already in transaction set'), po)
                    tx_return.extend(self.tsInfo.getMembers(pkgtup=po.pkgtup))
                    continue
            
            # make sure this shouldn't be passed to update:
            ipkgs = self.rpmdb.searchNames([po.name])
            if ipkgs and self._install_is_upgrade(po, ipkgs):
                txmbrs = self.update(po=po, repoid=kwargs.get('repoid'))
                tx_return.extend(txmbrs)
                continue

            if not self._valid_install_arch(po, ipkgs):
                continue
            
            #  Make sure we're not installing a package which is obsoleted by
            # something else in the repo. Unless there is a obsoletion loop,
            # at which point ignore everything.
            # NOTE: This is broken wrt. repoid...
            obsoleting_pkg = None
            if self.conf.obsoletes and not isinstance(po, YumLocalPackage):
                obsoleting_pkg = self._test_loop(po, self._pkg2obspkg)
            if obsoleting_pkg is not None:
                # this is not a definitive check but it'll make sure we don't
                # pull in foo.i586 when foo.x86_64 already obsoletes the pkg and
                # is already installed
                already_obs = None
                pkgs = self.rpmdb.searchNevra(name=obsoleting_pkg.name)
                pkgs = po.obsoletedBy(pkgs, limit=1)
                if pkgs:
                    already_obs = pkgs[0]

                if already_obs:
                    pkg_warn(_('Package %s is obsoleted by %s which is already installed'), 
                             po, already_obs)
                else:
                    if 'provides_for' in kwargs:
                        if not obsoleting_pkg.provides_for(kwargs['provides_for']):
                            pkg_warn(_('Package %s is obsoleted by %s, but obsoleting package does not provide for requirements'),
                                     po.name, obsoleting_pkg.name)
                            continue
                    pkg_warn(_('Package %s is obsoleted by %s, trying to install %s instead'),
                             po.name, obsoleting_pkg.name, obsoleting_pkg)
                    tx_return.extend(self.install(po=obsoleting_pkg))
                continue
            
            # make sure it's not already installed
            if self.rpmdb.contains(po=po):
                if not self.tsInfo.getMembersWithState(po.pkgtup, TS_REMOVE_STATES):
                    pkg_warn(_('Package %s already installed and latest version'), po)
                    continue

            # make sure we don't have a name.arch of this already installed
            # if so pass it to update b/c it should be able to figure it out
            # if self.rpmdb.contains(name=po.name, arch=po.arch) and not self.allowedMultipleInstalls(po):
            if not self.allowedMultipleInstalls(po):
                found = True
                for ipkg in self.rpmdb.searchNevra(name=po.name, arch=po.arch):
                    found = False
                    if self.tsInfo.getMembersWithState(ipkg.pkgtup, TS_REMOVE_STATES):
                        found = True
                        break
                if not found:
                    pkg_warn(_('Package matching %s already installed. Checking for update.'), po)            
                    txmbrs = self.update(po=po, repoid=kwargs.get('repoid'))
                    tx_return.extend(txmbrs)
                    continue

                
            # at this point we are going to mark the pkg to be installed, make sure
            # it's not an older package that is allowed in due to multiple installs
            # or some other oddity. If it is - then modify the problem filter to cope
            
            for ipkg in self.rpmdb.searchNevra(name=po.name, arch=po.arch):
                if ipkg.verEQ(po):
                    self._add_prob_flags(rpm.RPMPROB_FILTER_REPLACEPKG,
                                         rpm.RPMPROB_FILTER_REPLACENEWFILES,
                                         rpm.RPMPROB_FILTER_REPLACEOLDFILES)
                    #  Yum needs the remove to happen before we allow the
                    # install of the same version. But rpm doesn't like that
                    # as it then has an install which removes the old version
                    # and a remove, which also tries to remove the old version.
                    self.tsInfo.remove(ipkg.pkgtup)
                    break
            self._enable_oldpackage_flag(po)
            
            # it doesn't obsolete anything. If it does, mark that in the tsInfo, too
            obs_pkgs = list(self._find_obsoletees_direct(po))
            if obs_pkgs:
                for obsoletee in obs_pkgs:
                    txmbr = self.tsInfo.addObsoleting(po, obsoletee)
                    self.tsInfo.addObsoleted(obsoletee, po)
                    tx_return.append(txmbr)
            else:
                txmbr = self.tsInfo.addInstall(po)
                tx_return.append(txmbr)

        return tx_return

    def _check_new_update_provides(self, opkg, npkg):
        """ Check for any difference in the provides of the old and new update
            that is needed by the transaction. If so we "update" those pkgs
            too, to the latest version. """
        oprovs = set(opkg.returnPrco('provides'))
        nprovs = set(npkg.returnPrco('provides'))
        tx_return = []
        for prov in oprovs.difference(nprovs):
            reqs = self.tsInfo.getRequires(*prov)
            for pkg in reqs:
                for req in reqs[pkg]:
                    if not npkg.inPrcoRange('provides', req):
                        naTup = (pkg.name, pkg.arch)
                        for pkg in self.pkgSack.returnNewestByNameArch(naTup):
                            tx_return.extend(self.update(po=pkg))
                        break
        return tx_return

    def _newer_update_in_trans(self, pkgtup, available_pkg, tx_return):
        """ We return True if there is a newer package already in the
            transaction. If there is an older one, we remove it (and update any
            deps. that aren't satisfied by the newer pkg) and return False so
            we'll update to this newer pkg. """
        found = False
        for txmbr in self.tsInfo.getMembersWithState(pkgtup, [TS_UPDATED]):
            count = 0
            for po in txmbr.updated_by:
                if available_pkg.verLE(po):
                    count += 1
                else:
                    for ntxmbr in self.tsInfo.getMembers(po.pkgtup):
                        self.tsInfo.remove(ntxmbr.po.pkgtup)
                        txs = self._check_new_update_provides(ntxmbr.po,
                                                              available_pkg)
                        tx_return.extend(txs)
            if count:
                found = True
            else:
                self.tsInfo.remove(txmbr.po.pkgtup)
        return found

    def _add_up_txmbr(self, requiringPo, upkg, ipkg):
        txmbr = self.tsInfo.addUpdate(upkg, ipkg)
        if requiringPo:
            txmbr.setAsDep(requiringPo)
        if ('reason' in ipkg.yumdb_info and ipkg.yumdb_info.reason == 'dep'):
            txmbr.reason = 'dep'
        return txmbr

    def update(self, po=None, requiringPo=None, update_to=False, **kwargs):
        """Mark the specified items to be updated.  If a package
        object is given, mark it.  Else, if a package is specified by
        the keyword arguments, mark it.  Finally, if nothing is given,
        mark all installed packages to be updated.


        :param po: the package object to be marked for updating
        :param requiringPo: the package object that requires the
           upgrade
        :param update_to: if *update_to* is True, the update will only
           be run if it will update the given package to the given
           version.  For example, if the package foo-1-2 is installed,::

             updatePkgs(["foo-1-2"], update_to=False)
           will work identically to::
            
             updatePkgs(["foo"])
           but::

             updatePkgs(["foo-1-2"], update_to=True)
           
           will do nothing
        :param kwargs: if *po* is not given, the names or wildcards in
           *kwargs* will be used to find the packages to update
        :return: a list of transaction members added to the
           transaction set by this function
        """
        # check for args - if no po nor kwargs, do them all
        # if po, do it, ignore all else
        # if no po do kwargs
        # uninstalled pkgs called for update get returned with errors in a list, maybe?

        pkg_warn = kwargs.get('pkg_warning_level', 'flibble')
        def _dbg2(*args, **kwargs):
            self.verbose_logger.log(logginglevels.DEBUG_2, *args, **kwargs)
        level2func = {'debug2' : _dbg2,
                      'warning' : self.verbose_logger.warning}
        if pkg_warn not in level2func:
            pkg_warn = 'warning'
        pkg_warn = level2func[pkg_warn]

        tx_return = []
        if not po and not kwargs: # update everything (the easy case)
            self.verbose_logger.log(logginglevels.DEBUG_2, _('Updating Everything'))
            updates = self.up.getUpdatesTuples()
            if self.conf.obsoletes:
                obsoletes = self.up.getObsoletesTuples(newest=1)
            else:
                obsoletes = []

            for (obsoleting, installed) in obsoletes:
                obsoleting_pkg = self.getPackageObject(obsoleting,
                                                       allow_missing=True)
                if obsoleting_pkg is None:
                    continue
                installed_pkg =  self.getInstalledPackageObject(installed)
                if not self._valid_obsoleter_arch(obsoleting_pkg, installed_pkg):
                    continue
                topkg = self._test_loop(obsoleting_pkg, self._pkg2obspkg)
                if topkg is not None:
                    obsoleting_pkg = topkg
                txmbr = self.tsInfo.addObsoleting(obsoleting_pkg, installed_pkg)
                self.tsInfo.addObsoleted(installed_pkg, obsoleting_pkg)
                if requiringPo:
                    txmbr.setAsDep(requiringPo)
                tx_return.append(txmbr)
                
            for (new, old) in updates:
                if self.tsInfo.isObsoleted(pkgtup=old):
                    self.verbose_logger.log(logginglevels.DEBUG_2, _('Not Updating Package that is already obsoleted: %s.%s %s:%s-%s') %
                        old)
                else:
                    new = self.getPackageObject(new, allow_missing=True)
                    if new is None:
                        continue
                    tx_return.extend(self.update(po=new))

            # Upgrade the installed groups, as part of generic "yum upgrade"
            if (self.conf.group_command == 'objects' and
                self.conf.upgrade_group_objects_upgrade):
                for ievgrp in self.igroups.environments:
                    tx_return.extend(self._at_groupupgrade('@^' + ievgrp))
                for igrp in self.igroups.groups:
                    tx_return.extend(self._at_groupupgrade('@'  + igrp))

            return tx_return

        # complications
        # the user has given us something - either a package object to be
        # added to the transaction as an update or they've given us a pattern 
        # of some kind
        
        instpkgs = []
        availpkgs = []
        arch_specified = True
        if po: # just a po
            if po.repoid == 'installed':
                instpkgs.append(po)
            else:
                availpkgs.append(po)
                
                
        elif 'pattern' in kwargs:
            if kwargs['pattern'] and kwargs['pattern'][0] == '-':
                return self._minus_deselect(kwargs['pattern'])

            if kwargs['pattern'] and kwargs['pattern'][0] == '@':
                return self._at_groupupgrade(kwargs['pattern'])

            arg = kwargs['pattern']
            if not update_to:
                instpkgs  = self.rpmdb.returnPackages(patterns=[arg])
                instpkgs  = misc.filter_pkgs_repoid(instpkgs,
                                                    kwargs.get('repoid'))
            else:
                availpkgs = self.pkgSack.returnPackages(patterns=[arg],
                                                        repoid=kwargs.get('repoid'))

            if not instpkgs and not availpkgs:
                depmatches = []
                try:
                    if update_to:
                        depmatches = self.returnPackagesByDep(arg)
                    else:
                        depmatches = self.returnInstalledPackagesByDep(arg)
                except yum.Errors.YumBaseError, e:
                    self.logger.critical(_('%s') % e)

                depmatches = misc.filter_pkgs_repoid(depmatches,
                                                     kwargs.get('repoid'))
                if update_to:
                    availpkgs.extend(depmatches)
                else:
                    instpkgs.extend(depmatches)

            #  Always look for available packages, it doesn't seem to do any
            # harm (apart from some time). And it fixes weird edge cases where
            # "update a" (which requires a new b) is different from "update b"
            try:
                if update_to:
                    m = []
                elif kwargs.get('repoid'):
                    pats = [kwargs['pattern']]
                    m = self.pkgSack.sacks[kwargs['repoid']]
                    m = m.returnNewestByNameArch(patterns=pats)
                else:
                    pats = [kwargs['pattern']]
                    m = self.pkgSack.returnNewestByNameArch(patterns=pats)
            except Errors.PackageSackError:
                m = []
            availpkgs.extend(m)

            if not availpkgs and not instpkgs:
                self.logger.critical(_('No Match for argument: %s') % to_unicode(arg))
                if not self.conf.skip_missing_names_on_update:
                    raise Errors.UpdateMissingNameError, _('Not tolerating missing names on update, stopping.')

            arch_specified = '.' in kwargs['pattern']
        
        else: # we have kwargs, sort them out.
            nevra_dict = self._nevra_kwarg_parse(kwargs)

            instpkgs = self.rpmdb.searchNevra(name=nevra_dict['name'], 
                        epoch=nevra_dict['epoch'], arch=nevra_dict['arch'], 
                        ver=nevra_dict['version'], rel=nevra_dict['release'])

            if not instpkgs:
                availpkgs = self.pkgSack.searchNevra(name=nevra_dict['name'],
                            epoch=nevra_dict['epoch'], arch=nevra_dict['arch'],
                            ver=nevra_dict['version'], rel=nevra_dict['release'])
                self._add_not_found_a(availpkgs, nevra_dict)
                if len(availpkgs) > 1:
                    availpkgs = self._compare_providers(availpkgs, requiringPo)
                    availpkgs = map(lambda x: x[0], availpkgs)
                elif not availpkgs:
                    pkg_warn(_("No package matched to upgrade: %s"), self._ui_nevra_dict(nevra_dict))
       
        # for any thing specified
        # get the list of available pkgs matching it (or take the po)
        # get the list of installed pkgs matching it (or take the po)
        # go through each list and look for:
           # things obsoleting it if it is an installed pkg
           # things it updates if it is an available pkg
           # things updating it if it is an installed pkg
           # in that order
           # all along checking to make sure we:
            # don't update something that's already been obsoleted
            # don't update something that's already been updated
            
        # if there are more than one package that matches an update from
        # a pattern/kwarg then:
            # if it is a valid update and we'
        
        # TODO: we should search the updates and obsoletes list and
        # mark the package being updated or obsoleted away appropriately
        # and the package relationship in the tsInfo
        

        # check for obsoletes first
        if self.conf.obsoletes:
            for installed_pkg in instpkgs:
                obs_tups = self.up.obsoleted_dict.get(installed_pkg.pkgtup, [])
                # This is done so we don't have to returnObsoletes(newest=True)
                # It's a minor UI problem for RHEL, but might as well dtrt.
                obs_pkgs = []
                for pkgtup in obs_tups:
                    obsoleting_pkg = self.getPackageObject(pkgtup,
                                                           allow_missing=True)
                    if obsoleting_pkg is None:
                        continue
                    if not arch_specified and not self._valid_obsoleter_arch(obsoleting_pkg, installed_pkg):
                        continue
                    obs_pkgs.append(obsoleting_pkg)
                # NOTE: Broekn wrt. repoid
                for obsoleting_pkg in packagesNewestByName(obs_pkgs):
                    tx_return.extend(self.install(po=obsoleting_pkg))
            for available_pkg in availpkgs:
                for obsoleted_pkg in self._find_obsoletees(available_pkg):
                    if not arch_specified and not self._valid_obsoleter_arch(available_pkg, obsoleted_pkg):
                        continue
                    obsoleted = obsoleted_pkg.pkgtup
                    txmbr = self.tsInfo.addObsoleting(available_pkg, obsoleted_pkg)
                    if requiringPo:
                        txmbr.setAsDep(requiringPo)
                    tx_return.append(txmbr)
                    if self.tsInfo.isObsoleted(obsoleted):
                        self.verbose_logger.log(logginglevels.DEBUG_2, _('Package is already obsoleted: %s.%s %s:%s-%s') % obsoleted)
                    else:
                        txmbr = self.tsInfo.addObsoleted(obsoleted_pkg, available_pkg)
                        tx_return.append(txmbr)

        for installed_pkg in instpkgs:
            for updating in self.up.updatesdict.get(installed_pkg.pkgtup, []):
                po = self.getPackageObject(updating, allow_missing=True)
                if po is None:
                    continue
                if self.tsInfo.isObsoleted(installed_pkg.pkgtup):
                    self.verbose_logger.log(logginglevels.DEBUG_2, _('Not Updating Package that is already obsoleted: %s.%s %s:%s-%s') %
                                            installed_pkg.pkgtup)                                               
                # at this point we are going to mark the pkg to be installed, make sure
                # it doesn't obsolete anything. If it does, mark that in the tsInfo, too
                elif po.pkgtup in self.up.getObsoletesList(name=po.name):
                    for obsoletee in self._find_obsoletees(po):
                        txmbr = self.tsInfo.addUpdate(po, installed_pkg)
                        if requiringPo:
                            txmbr.setAsDep(requiringPo)
                        self.tsInfo.addObsoleting(po, obsoletee)
                        self.tsInfo.addObsoleted(obsoletee, po)
                        tx_return.append(txmbr)
                else:
                    if self.tsInfo.getMembersWithState(installed_pkg.pkgtup,
                                                       TS_REMOVE_STATES):
                        self.tsInfo.remove(installed_pkg.pkgtup)
                    txmbr = self._add_up_txmbr(requiringPo, po, installed_pkg)
                    tx_return.append(txmbr)
                        
        for available_pkg in availpkgs:
            if not self._valid_install_arch(available_pkg):
                continue

            # "Just do it" if it's a local pkg.
            if isinstance(available_pkg, YumLocalPackage):
                n = available_pkg.name
                for updated_pkg in self.rpmdb.returnNewestByName(n):
                    updated = updated_pkg.pkgtup
                    if self.tsInfo.getMembersWithState(updated,
                                                       TS_REMOVE_STATES):
                        self.tsInfo.remove(updated)
                    txmbr = self._add_up_txmbr(requiringPo,
                                               available_pkg, updated_pkg)
                    tx_return.append(txmbr)
                continue
            #  Make sure we're not installing a package which is obsoleted by
            # something else in the repo. Unless there is a obsoletion loop,
            # at which point ignore everything.
            obsoleting_pkg = self._test_loop(available_pkg, self._pkg2obspkg)
            if obsoleting_pkg is not None:
                if (kwargs.get('repoid') and
                    obsoleting_pkg.repoid != kwargs.get('repoid')):
                    continue # Meh.
                self.verbose_logger.log(logginglevels.DEBUG_2, _('Not Updating Package that is obsoleted: %s'), available_pkg)
                tx_return.extend(self.update(po=obsoleting_pkg))
                continue
            for updated in self.up.updating_dict.get(available_pkg.pkgtup, []):
                if self.tsInfo.isObsoleted(updated):
                    self.verbose_logger.log(logginglevels.DEBUG_2, _('Not Updating Package that is already obsoleted: %s.%s %s:%s-%s') %
                                            updated)
                elif self._newer_update_in_trans(updated, available_pkg,
                                                 tx_return):
                    self.verbose_logger.log(logginglevels.DEBUG_2, _('Not Updating Package that is already updated: %s.%s %s:%s-%s') %
                                            updated)
                
                else:
                    updated_pkg =  self.getInstalledPackageObject(updated)
                    if self.tsInfo.getMembersWithState(updated,
                                                       TS_REMOVE_STATES):
                        self.tsInfo.remove(updated)
                    txmbr = self._add_up_txmbr(requiringPo,
                                               available_pkg, updated_pkg)
                    tx_return.append(txmbr)
                    
            # check to see if the pkg we want to install is not _quite_ the newest
            # one but still technically an update over what is installed.
            pot_updated = self.rpmdb.searchNevra(name=available_pkg.name, arch=available_pkg.arch)
            if pot_updated and self.allowedMultipleInstalls(available_pkg):
                # only compare against the newest of what's installed for kernel
                pot_updated = sorted(pot_updated)[-1:]
#FIXME - potentially do the comparables thing from what used to
#        be in cli.installPkgs() to see what we should be comparing
#        it to of what is installed. in the meantime name.arch is
#        most likely correct
# this is sorta a fix - but it shouldn't be only for localPackages
#            else:
#                if available_pkg in self.localPackages:
#                    # if we got here the potentially updated is not a matching arch
#                    # and we're goofed up in a localPackage that someone wants to apply for some odd reason
#                    # so we go for name-only update match and check
#                    pot_updated = self.rpmdb.searchNevra(name=available_pkg.name)
            
            for ipkg in pot_updated:
                if self.tsInfo.isObsoleted(ipkg.pkgtup):
                    self.verbose_logger.log(logginglevels.DEBUG_2, _('Not Updating Package that is already obsoleted: %s.%s %s:%s-%s') %
                                            ipkg.pkgtup)
                elif self._newer_update_in_trans(ipkg.pkgtup, available_pkg,
                                                 tx_return):
                    self.verbose_logger.log(logginglevels.DEBUG_2, _('Not Updating Package that is already updated: %s.%s %s:%s-%s') %
                                            ipkg.pkgtup)
                elif ipkg.verLT(available_pkg):
                    txmbr = self._add_up_txmbr(requiringPo, available_pkg, ipkg)
                    tx_return.append(txmbr)
        
        for txmbr in tx_return:
            for i_pkg in self.rpmdb.searchNevra(name=txmbr.name):
                if i_pkg not in txmbr.updates:
                    if self._does_this_update(txmbr.po, i_pkg):
                        self.tsInfo.addUpdated(i_pkg, txmbr.po)
                        
        return tx_return
        
    def remove(self, po=None, **kwargs):
        """Mark the specified packages for removal. If a package
        object is given, mark it for removal.  Otherwise, mark the
        package specified by the keyword arguments.

        :param po: the package object to mark for installation
        :param kwargs: If *po* is not given, the keyword arguments
           will be used to specify a package to mark for installation
        :return: a list of the transaction members that were added to
           the transaction set by this method
        :raises: :class:`yum.Errors.RemoveError` if nothing is specified
           to mark for removal
        """
        if not po and not kwargs:
            raise Errors.RemoveError, 'Nothing specified to remove'
        
        tx_return = []
        pkgs = []
        
        
        if po:
            pkgs = [po]  
        else:
            if 'pattern' in kwargs:
                if kwargs['pattern'] and kwargs['pattern'][0] == '-':
                    return self._minus_deselect(kwargs['pattern'])

                if kwargs['pattern'] and kwargs['pattern'][0] == '@':
                    return self._at_groupremove(kwargs['pattern'])

                (e,m,u) = self.rpmdb.matchPackageNames([kwargs['pattern']])
                if 'repoid' in kwargs:
                    e = misc.filter_pkgs_repoid(e, kwargs['repoid'])
                    m = misc.filter_pkgs_repoid(m, kwargs['repoid'])

                pkgs.extend(e)
                pkgs.extend(m)
                if u:
                    depmatches = []
                    arg = u[0]
                    try:
                        depmatches = self.returnInstalledPackagesByDep(arg)
                    except yum.Errors.YumBaseError, e:
                        self.logger.critical(_('%s') % e)
                    
                    if 'repoid' in kwargs:
                        depmatches = misc.filter_pkgs_repoid(depmatches,
                                                             kwargs['repoid'])

                    if not depmatches:
                        arg = to_unicode(arg)
                        self.logger.critical(_('No Match for argument: %s') % to_unicode(arg))
                    else:
                        pkgs.extend(depmatches)
                
            else:    
                nevra_dict = self._nevra_kwarg_parse(kwargs)

                pkgs = self.rpmdb.searchNevra(name=nevra_dict['name'], 
                            epoch=nevra_dict['epoch'], arch=nevra_dict['arch'], 
                            ver=nevra_dict['version'], rel=nevra_dict['release'])
                self._add_not_found_i(pkgs, nevra_dict)
                if len(pkgs) == 0:
                    if not kwargs.get('silence_warnings', False):
                        self.logger.warning(_("No package matched to remove: %s"), self._ui_nevra_dict(nevra_dict))

        ts = self.rpmdb.readOnlyTS()
        kern_pkgtup = misc.get_running_kernel_pkgtup(ts)
        for po in pkgs:
            if self.conf.protected_packages and po.pkgtup == kern_pkgtup:
                self.logger.warning(_("Skipping the running kernel: %s") % po)
                continue

            if self.tsInfo.getMembers(po.pkgtup):
                # This allows multiple reinstalls and update/downgrade "cancel"
                for txmbr in self.tsInfo.matchNaevr(po.name):
                    self.logger.info(_("Removing %s from the transaction") %
                                     txmbr)
                    self.tsInfo.remove(txmbr.pkgtup)
                # Now start the remove/reinstall
            txmbr = self.tsInfo.addErase(po)
            tx_return.append(txmbr)
        
        return tx_return

    def installLocal(self, pkg, po=None, updateonly=False):
        """Mark a package on the local filesystem (i.e. not from a
        repository) for installation. 
        
        :param pkg: a string specifying the path to an rpm file in the
           local filesystem to be marked for installation
        :param po: a :class:`yum.packages.YumLocalPackage` 
        :param updateonly: if True, the given package will only be
           marked for installation if it is an upgrade for a package
           that is already installed.  If False, this restriction is
           not enforced
        :return: a list of the transaction members added to the
           transaction set by this method
        """
        # read in the package into a YumLocalPackage Object
        # append it to self.localPackages
        # check if it can be installed or updated based on nevra versus rpmdb
        # don't import the repos until we absolutely need them for depsolving
        tx_return = []
        installpkgs = []
        updatepkgs = []
        donothingpkgs = []

        if not po:
            try:
                po = YumUrlPackage(self, ts=self.rpmdb.readOnlyTS(), url=pkg,
                                   ua=default_grabber.opts.user_agent)
            except Errors.MiscError:
                self.logger.critical(_('Cannot open: %s. Skipping.'), pkg)
                return tx_return
            self.verbose_logger.log(logginglevels.INFO_2,
                _('Examining %s: %s'), po.localpath, po)

        # apparently someone wanted to try to install a drpm as an rpm :(
        if po.hdr['payloadformat'] == 'drpm':
            self.logger.critical(_('Cannot localinstall deltarpm: %s. Skipping.'), pkg)
            return tx_return

        # if by any chance we're a noncompat arch rpm - bail and throw out an error
        # FIXME -our archlist should be stored somewhere so we don't have to
        # do this: but it's not a config file sort of thing
        # FIXME: Should add noarch, yum localinstall works ...
        # just rm this method?
        if po.arch not in self.arch.archlist:
            self.logger.critical(_('Cannot add package %s to transaction. Not a compatible architecture: %s'), pkg, po.arch)
            return tx_return
        
        if self.conf.obsoletes:
            obsoleters = po.obsoletedBy(self.rpmdb.searchObsoletes(po.name))
            if obsoleters:
                self.logger.critical(_('Cannot install package %s. It is obsoleted by installed package %s'), po, obsoleters[0])
                return tx_return
            
        # everything installed that matches the name
        installedByKey = self.rpmdb.searchNevra(name=po.name)
        # go through each package
        if len(installedByKey) == 0: # nothing installed by that name
            if updateonly:
                self.logger.warning(_('Package %s not installed, cannot update it. Run yum install to install it instead.'), po.name)
                return tx_return
            else:
                installpkgs.append(po)

        for installed_pkg in installedByKey:
            if po.verGT(installed_pkg) and not self.allowedMultipleInstalls(po):
                # we're newer - this is an update, pass to them
                if installed_pkg.name in self.conf.exactarchlist:
                    if po.arch == installed_pkg.arch:
                        updatepkgs.append((po, installed_pkg))
                    else:
                        donothingpkgs.append(po)
                else:
                    updatepkgs.append((po, installed_pkg))
            elif po.verEQ(installed_pkg):
                if (po.arch != installed_pkg.arch and
                    (isMultiLibArch(po.arch) or
                     isMultiLibArch(installed_pkg.arch))):
                    if updateonly:
                        self.logger.warning(_('Package %s.%s not installed, cannot update it. Run yum install to install it instead.'), po.name, po.arch)
                    else:
                        installpkgs.append(po)
                else:
                    donothingpkgs.append(po)
            elif self.allowedMultipleInstalls(po):
                if updateonly:
                    self.logger.warning(_('Package %s.%s not installed, cannot update it. Run yum install to install it instead.'), po.name, po.arch)
                else:
                    installpkgs.append(po)
            else:
                donothingpkgs.append(po)

        # handle excludes for a localinstall
        check_pkgs = installpkgs + [x[0] for x in updatepkgs]
        if self._is_local_exclude(po, check_pkgs):
            self.verbose_logger.debug(_('Excluding %s'), po)
            return tx_return

        for po in installpkgs:
            self.verbose_logger.log(logginglevels.INFO_2,
                _('Marking %s to be installed'), po.localpath)
            self.localPackages.append(po)
            tx_return.extend(self.install(po=po))

        for (po, oldpo) in updatepkgs:
            self.verbose_logger.log(logginglevels.INFO_2,
                _('Marking %s as an update to %s'), po.localpath, oldpo)
            self.localPackages.append(po)
            txmbrs = self.update(po=po)
            tx_return.extend(txmbrs)

        for po in donothingpkgs:
            self.verbose_logger.log(logginglevels.INFO_2,
                _('%s: does not update installed package.'), po.localpath)
        
        # this checks to make sure that any of the to-be-installed pkgs
        # does not obsolete something else that's installed
        # this doesn't handle the localpkgs obsoleting EACH OTHER or
        # anything else in the transaction set, though. That could/should
        # be fixed later but a fair bit of that is a pebkac and should be
        # said as "don't do that". potential 'fixme'
        for txmbr in tx_return:
            #  We don't want to do this twice, so only bother if the txmbr
            # doesn't already obsolete anything.
            if txmbr.po.obsoletes and not txmbr.obsoletes:
                for obs_pkg in self._find_obsoletees(txmbr.po):
                    self.tsInfo.addObsoleted(obs_pkg, txmbr.po)
                    txmbr.obsoletes.append(obs_pkg)
                    self.tsInfo.addObsoleting(txmbr.po,obs_pkg)
                
        return tx_return

    def reinstallLocal(self, pkg, po=None):
        """Mark a package on the local filesystem (i.e. not from a
        repository) for reinstallation. 
        
        :param pkg: a string specifying the path to an rpm file in the
           local filesystem to be marked for reinstallation
        :param po: a :class:`yum.packages.YumLocalPackage` 
        :return: a list of the transaction members added to the
           transaction set by this method
        """
        if not po:
            try:
                po = YumUrlPackage(self, ts=self.rpmdb.readOnlyTS(), url=pkg,
                                   ua=default_grabber.opts.user_agent)
            except Errors.MiscError:
                self.logger.critical(_('Cannot open file: %s. Skipping.'), pkg)
                return []
            self.verbose_logger.log(logginglevels.INFO_2,
                _('Examining %s: %s'), po.localpath, po)

        if po.arch not in self.arch.archlist:
            self.logger.critical(_('Cannot add package %s to transaction. Not a compatible architecture: %s'), pkg, po.arch)
            return []

        # handle excludes for a local reinstall
        if self._is_local_exclude(po, [po]):
            self.verbose_logger.debug(_('Excluding %s'), po)
            return []

        return self.reinstall(po=po)

    def reinstall(self, po=None, **kwargs):
        """Mark the given package for reinstallation.  This is
        accomplished by setting problem filters to allow a reinstall
        take place, then calling :func:`install`.

        :param po: the package object to mark for reinstallation
        :param kwargs: if po is not given, the keyword will be used to
           specify a package for reinstallation
        :return: a list of the transaction members added to the
           transaction set by this method
        :raises: :class:`yum.Errors.ReinstallRemoveError` or
           :class:`yum.Errors.ReinstallInstallError` depending the nature
           of the error that is encountered
        """
        self._add_prob_flags(rpm.RPMPROB_FILTER_REPLACEPKG,
                             rpm.RPMPROB_FILTER_REPLACENEWFILES,
                             rpm.RPMPROB_FILTER_REPLACEOLDFILES)

        # NOTE: For repoid=foo we could do two things:
        # 1. Only do the "remove" op. with packages installed from "foo".
        # 2. Only do the "install" op. with packages available from "foo".
        # ...so repoid=foo means #1 and repoid_install=foo means #2. Can also
        # combine them.

        tx_mbrs = []
        if po: # The po, is the "available" po ... we want the installed po
            tx_mbrs.extend(self.remove(pkgtup=po.pkgtup))
        else:
            tx_mbrs.extend(self.remove(**kwargs))
        if not tx_mbrs:
            raise Errors.ReinstallRemoveError, _("Problem in reinstall: no package matched to remove")
        templen = len(tx_mbrs)
        # this is a reinstall, so if we can't reinstall exactly what we uninstalled
        # then we really shouldn't go on
        new_members = []
        failed = []
        failed_pkgs = []
        for item in tx_mbrs[:]:
            #  Make sure obsoletes processing is off, so we can reinstall()
            # pkgs that are obsolete.
            old_conf_obs = self.conf.obsoletes
            self.conf.obsoletes = False
            if isinstance(po, YumLocalPackage) and 'repoid' not in kwargs:
                members = self.install(po=po)
            else:
                members = self.install(pkgtup=item.pkgtup,
                                       repoid=kwargs.get('repoid_install'))
            self.conf.obsoletes = old_conf_obs
            if len(members) == 0:
                self.tsInfo.remove(item.pkgtup)
                tx_mbrs.remove(item)
                failed.append(str(item.po))
                failed_pkgs.append(item.po)
                continue
            new_members.extend(members)

        if failed and not tx_mbrs:
            raise Errors.ReinstallInstallError(_("Problem in reinstall: no package %s matched to install") % ", ".join(failed), failed_pkgs=failed_pkgs)
        tx_mbrs.extend(new_members)
        return tx_mbrs
        
    def downgradeLocal(self, pkg, po=None):
        """Mark a package on the local filesystem (i.e. not from a
        repository) to be downgraded.
        
        :param pkg: a string specifying the path to an rpm file in the
           local filesystem to be marked to be downgraded
        :param po: a :class:`yum.packages.YumLocalPackage` 
        :return: a list of the transaction members added to the
           transaction set by this method
        """
        if not po:
            try:
                po = YumUrlPackage(self, ts=self.rpmdb.readOnlyTS(), url=pkg,
                                   ua=default_grabber.opts.user_agent)
            except Errors.MiscError:
                self.logger.critical(_('Cannot open file: %s. Skipping.'), pkg)
                return []
            self.verbose_logger.log(logginglevels.INFO_2,
                _('Examining %s: %s'), po.localpath, po)

        if po.arch not in self.arch.archlist:
            self.logger.critical(_('Cannot add package %s to transaction. Not a compatible architecture: %s'), pkg, po.arch)
            return []

        # handle excludes for a local downgrade
        if self._is_local_exclude(po, [po]):
            self.verbose_logger.debug(_('Excluding %s'), po)
            return []

        return self.downgrade(po=po)

    def _is_local_exclude(self, po, pkglist):
        """returns True if the local pkg should be excluded"""
        
        if "all" in self.conf.disable_excludes or \
           "main" in self.conf.disable_excludes:
            return False
        
        toexc = []
        if len(self.conf.exclude) > 0:
            exactmatch, matched, unmatched = \
                   parsePackages(pkglist, self.conf.exclude, casematch=1)
            toexc = exactmatch + matched

        if po in toexc:
            return True

        return False
        
    def downgrade(self, po=None, **kwargs):
        """Mark a package to be downgraded.  This is equivalent to
        first removing the currently installed package, and then
        installing the older version.

        :param po: the package object to be marked to be downgraded
        :param kwargs: if a package object is not given, the keyword
           arguments will be used to specify a package to be marked to
           be downgraded
        :return: a list of the transaction members added to the
           transaction set by this method
        :raises: :class:`yum.Errors.DowngradeError` if no packages are
           specified or available for downgrade
        """
        if not po and not kwargs:
            raise Errors.DowngradeError, 'Nothing specified to downgrade'

        doing_group_pkgs = False
        if po:
            apkgs = [po]
        elif 'pattern' in kwargs:
            if kwargs['pattern'] and kwargs['pattern'][0] == '-':
                return self._minus_deselect(kwargs['pattern'])

            if kwargs['pattern'] and kwargs['pattern'][0] == '@':
                apkgs = self._at_groupdowngrade(kwargs['pattern'])
                doing_group_pkgs = True # Don't warn. about some things
            else:
                apkgs = self.pkgSack.returnPackages(patterns=[kwargs['pattern']],
                                                   ignore_case=False)
                if not apkgs:
                    arg = kwargs['pattern']
                    self.verbose_logger.debug(_('Checking for virtual provide or file-provide for %s'), 
                        arg)

                    try:
                        apkgs = self.returnPackagesByDep(arg)
                    except yum.Errors.YumBaseError, e:
                        self.logger.critical(_('No Match for argument: %s') % to_unicode(arg))

        else:
            nevra_dict = self._nevra_kwarg_parse(kwargs)
            apkgs = self.pkgSack.searchNevra(name=nevra_dict['name'], 
                                             epoch=nevra_dict['epoch'],
                                             arch=nevra_dict['arch'], 
                                             ver=nevra_dict['version'],
                                             rel=nevra_dict['release'])
            self._add_not_found_a(apkgs, nevra_dict)

        if not apkgs:
            # Do we still want to return errors here?
            # We don't in the cases below, so I didn't here...
            pkgs = []
            if 'pattern' in kwargs:
                pkgs = self.rpmdb.returnPackages(patterns=[kwargs['pattern']],
                                                 ignore_case=False)
            if 'name' in kwargs:
                pkgs = self.rpmdb.searchNevra(name=kwargs['name'])
            if pkgs:
                return []
            raise Errors.DowngradeError, _('No package(s) available to downgrade')

        warned_nas = set()
        # Skip kernel etc.
        tapkgs = []
        for pkg in apkgs:
            if self.allowedMultipleInstalls(pkg):
                if (pkg.name, pkg.arch) not in warned_nas:
                    msg = _("Package %s is allowed multiple installs, skipping") % pkg
                    self.verbose_logger.log(logginglevels.INFO_2, msg)
                warned_nas.add((pkg.name, pkg.arch))
                continue
            tapkgs.append(pkg)
        apkgs = tapkgs

        # Find installed versions of "to downgrade pkgs"
        apkg_names = set()
        for pkg in apkgs:
            apkg_names.add(pkg.name)
        ipkgs = self.rpmdb.searchNames(list(apkg_names))

        latest_installed_na = {}
        latest_installed_n  = {}
        for pkg in sorted(ipkgs):
            if (pkg.name not in latest_installed_n or
                pkg.verGT(latest_installed_n[pkg.name][0])):
                latest_installed_n[pkg.name] = [pkg]
            elif pkg.verEQ(latest_installed_n[pkg.name][0]):
                latest_installed_n[pkg.name].append(pkg)
            latest_installed_na[(pkg.name, pkg.arch)] = pkg

        #  Find "latest downgrade", ie. latest available pkg before
        # installed version. Indexed fromn the latest installed pkgtup.
        downgrade_apkgs = {}
        for pkg in sorted(apkgs):
            # We are cleverer here, I think...
            # if not self._valid_install_arch(pkg, ipkgs):
            # continue

            na  = (pkg.name, pkg.arch)

            # Here we allow downgrades from .i386 => .noarch, or .i586 => .i386
            # but not .i386 => .x86_64 (similar to update).
            lipkg = None
            if na in latest_installed_na:
                lipkg = latest_installed_na[na]
            elif pkg.name in latest_installed_n:
                for tlipkg in latest_installed_n[pkg.name]:
                    if not canCoinstall(pkg.arch, tlipkg.arch):
                        lipkg = tlipkg
                        #  Use this so we don't get confused when we have
                        # different versions with different arches.
                        na = (pkg.name, lipkg.arch)
                        break

            if lipkg is None:
                if (na not in warned_nas and not doing_group_pkgs and
                    pkg.name not in latest_installed_n):
                    msg = _('No Match for available package: %s') % pkg
                    self.logger.critical(msg)
                warned_nas.add(na)
                continue

            if pkg.verEQ(lipkg):
                continue

            if pkg.verGE(lipkg):
                if na not in warned_nas:
                    msg = _('Only Upgrade available on package: %s') % pkg
                    self.logger.critical(msg)
                warned_nas.add(na)
                continue

            warned_nas.add(na)
            if (lipkg.pkgtup in downgrade_apkgs and
                pkg.verLE(downgrade_apkgs[lipkg.pkgtup])):
                continue # Skip older than "latest downgrade"
            downgrade_apkgs[lipkg.pkgtup] = pkg

        tx_return = []
        for ipkg in ipkgs:
            if ipkg.pkgtup not in downgrade_apkgs:
                continue
            txmbrs = self.tsInfo.addDowngrade(downgrade_apkgs[ipkg.pkgtup],ipkg)
            if not txmbrs: # Fail?
                continue
            self._add_prob_flags(rpm.RPMPROB_FILTER_OLDPACKAGE)
            tx_return.extend(txmbrs)

        return tx_return

    @staticmethod
    def _ui_nevra_dict(nevra_dict):
        n = nevra_dict['name']
        e = nevra_dict['epoch']
        v = nevra_dict['version']
        r = nevra_dict['release']
        a = nevra_dict['arch']

        if e and v and r:
            evr = '%s:%s-%s' % (e, v, r)
        elif v and r:
            evr = '%s-%s' % (v, r)
        elif e and v:
            evr = '%s:%s' % (e, v)
        elif v: # e and r etc. is just too weird to print
            evr = v
        else:
            evr = ''
        if n and evr:
            return '%s-%s' % (n, evr)
        if evr:
            return '*-%s' % evr
        if n:
            return n
        return '<unknown>'
        
    def _nevra_kwarg_parse(self, kwargs):
            
        returndict = {}
        
        if 'pkgtup' in kwargs:
            (n, a, e, v, r) = kwargs['pkgtup']
            returndict['name'] = n
            returndict['epoch'] = e
            returndict['arch'] = a
            returndict['version'] = v
            returndict['release'] = r
            return returndict

        returndict['name'] = kwargs.get('name')
        returndict['epoch'] = kwargs.get('epoch')
        returndict['arch'] = kwargs.get('arch')
        # get them as ver, version and rel, release - if someone
        # specifies one of each then that's kinda silly.
        returndict['version'] = kwargs.get('version')
        if returndict['version'] is None:
            returndict['version'] = kwargs.get('ver')

        returndict['release'] = kwargs.get('release')
        if returndict['release'] is None:
            returndict['release'] = kwargs.get('rel')

        return returndict

    def history_redo(self, transaction,
                     force_reinstall=False, force_changed_removal=False):
        """Repeat the transaction represented by the given
        :class:`yum.history.YumHistoryTransaction` object.

        :param transaction: a
           :class:`yum.history.YumHistoryTransaction` object
           representing the transaction to be repeated
        :param force_reinstall: bool - do we want to reinstall anything that was
           installed/updated/downgraded/etc.
        :param force_changed_removal: bool - do we want to force remove anything
           that was downgraded or upgraded.
        :return: whether the transaction was repeated successfully
        """
        # NOTE: This is somewhat basic atm. ... see comment in undo.
        #  Also note that redo doesn't force install Dep-Install packages,
        # which is probably what is wanted the majority of the time.

        old_conf_obs = self.conf.obsoletes
        self.conf.obsoletes = False
        done = False
        for pkg in transaction.trans_data:
            if pkg.state == 'Reinstall':
                if self.reinstall(pkgtup=pkg.pkgtup):
                    done = True
        for pkg in transaction.trans_data:
            if pkg.state == 'Downgrade':
                if force_reinstall and self.rpmdb.searchPkgTuple(pkg.pkgtup):
                    if self.reinstall(pkgtup=pkg.pkgtup):
                        done = True
                    continue

                try:
                    if self.downgrade(pkgtup=pkg.pkgtup):
                        done = True
                except yum.Errors.DowngradeError:
                    self.logger.critical(_('Failed to downgrade: %s'), pkg)
        for pkg in transaction.trans_data:
            if force_changed_removal and pkg.state == 'Downgraded':
                if self.tsInfo.getMembers(pkg.pkgtup):
                    continue
                if self.remove(pkgtup=pkg.pkgtup, silence_warnings=True):
                    done = True
        for pkg in transaction.trans_data:
            if pkg.state == 'Update':
                if force_reinstall and self.rpmdb.searchPkgTuple(pkg.pkgtup):
                    if self.reinstall(pkgtup=pkg.pkgtup):
                        done = True
                    continue

                if self.update(pkgtup=pkg.pkgtup):
                    done = True
                else:
                    self.logger.critical(_('Failed to upgrade: %s'), pkg)
        for pkg in transaction.trans_data:
            if force_changed_removal and pkg.state == 'Updated':
                if self.tsInfo.getMembers(pkg.pkgtup):
                    continue
                if self.remove(pkgtup=pkg.pkgtup, silence_warnings=True):
                    done = True
        for pkg in transaction.trans_data:
            if pkg.state in ('Install', 'True-Install', 'Obsoleting'):
                if force_reinstall and self.rpmdb.searchPkgTuple(pkg.pkgtup):
                    if self.reinstall(pkgtup=pkg.pkgtup):
                        done = True
                    continue

                if self.install(pkgtup=pkg.pkgtup):
                    done = True
        for pkg in transaction.trans_data:
            if pkg.state == 'Erase':
                if self.remove(pkgtup=pkg.pkgtup):
                    done = True
        self.conf.obsoletes = old_conf_obs
        return done

    def history_undo(self, transaction):
        """Undo the transaction represented by the given
        :class:`yum.history.YumHistoryTransaction` object.

        :param transaction: a
           :class:`yum.history.YumHistoryTransaction` object
           representing the transaction to be undone
        :return: whether the transaction was undone successfully
        """
        # NOTE: This is somewhat basic atm. ... for instance we don't check
        #       that we are going from the old new version. However it's still
        #       better than the RHN rollback code, and people pay for that :).
        #  We turn obsoletes off because we want the specific versions of stuff
        # from history ... even if they've been obsoleted since then.
        old_conf_obs = self.conf.obsoletes
        self.conf.obsoletes = False
        done = False
        for pkg in transaction.trans_data:
            if pkg.state == 'Reinstall':
                if self.reinstall(pkgtup=pkg.pkgtup):
                    done = True
        for pkg in transaction.trans_data:
            if pkg.state == 'Updated':
                try:
                    if self.downgrade(pkgtup=pkg.pkgtup):
                        done = True
                except yum.Errors.DowngradeError:
                    self.logger.critical(_('Failed to downgrade: %s'), pkg)
        for pkg in transaction.trans_data:
            if pkg.state == 'Downgraded':
                if self.update(pkgtup=pkg.pkgtup):
                    done = True
                else:
                    self.logger.critical(_('Failed to upgrade: %s'), pkg)
        for pkg in transaction.trans_data:
            if pkg.state == 'Obsoleting':
                #  Note that obsoleting can mean anything, so if this is part of
                # something else, it should be done by now (so do nothing).
                if self.tsInfo.getMembers(pkg.pkgtup):
                    continue
                #  If not it should be an install/obsolete ... so remove it.
                if self.remove(pkgtup=pkg.pkgtup):
                    done = True
        for pkg in transaction.trans_data:
            if pkg.state in ('Dep-Install', 'Install', 'True-Install'):
                if self.remove(pkgtup=pkg.pkgtup):
                    done = True
        for pkg in transaction.trans_data:
            if pkg.state == 'Obsoleted':
                if self.install(pkgtup=pkg.pkgtup):
                    done = True
        for pkg in transaction.trans_data:
            if pkg.state == 'Erase':
                if self.install(pkgtup=pkg.pkgtup):
                    done = True
        self.conf.obsoletes = old_conf_obs
        return done

    def redirect_failure_callback(self, data):
        """Failure callback for urlgrabber to force a retry if we time out
        (code 12) or error out (code 14) after being redirected (since these
        codes are not in opts.retrycodes).

        This allows for failovers if the URL points to a MirrorManager2 (such
        as download.fedoraproject.org).  If the mirror it redirects to is down
        for some reason, this will ensure that we try again, hopefully getting
        a mirror that works.
        """
        e = data.exception
        url_initial = data.url
        url_actual = e.url
        if (e.errno not in (12, 14) or url_initial == url_actual):
            # Not a timeout/HTTPError, or there was no redirect, so leave it up
            # to urlgrabber
            return
        if e.errno == 12:
            msg = _('Timeout on %s, trying again') % url_actual
        else:
            msg = _('Could not retrieve %s: %s, trying again') % (url_actual, e)
        # Force a retry by hacking the errno so that it falls within retrycodes
        e.errno = -1
        self.logger.error(msg)

    def _retrievePublicKey(self, keyurl, repo=None, getSig=True):
        """
        Retrieve a key file
        @param keyurl: url to the key to retrieve
        Returns a list of dicts with all the keyinfo
        """
        key_installed = False
        cb = self.redirect_failure_callback
        
        msg = _('Retrieving key from %s') % keyurl
        self.verbose_logger.log(logginglevels.INFO_2, msg)
       
        # Go get the GPG key from the given URL
        try:
            url = misc.to_utf8(keyurl)
            if repo is None:
                opts = {'limit':9999}
                text = 'global/gpgkey'
            else:
                #  If we have a repo. use the proxy etc. configuration for it.
                # In theory we have a global proxy config. too, but meh...
                # external callers should just update.
                opts = repo._default_grabopts()
                text = repo.id + '/gpgkey'
            rawkey = urlgrabber.urlread(url, failure_callback=cb, **opts)

        except urlgrabber.grabber.URLGrabError, e:
            raise Errors.YumBaseError(_('GPG key retrieval failed: ') +
                                      exception2msg(e))
                                      
        # check for a .asc file accompanying it - that's our gpg sig on the key
        # suck it down and do the check
        sigfile = None
        valid_sig = False
        if getSig and repo and repo.gpgcakey:
            self.getCAKeyForRepo(repo, callback=repo.confirm_func)
            try:
                url = misc.to_utf8(keyurl + '.asc')
                opts = repo._default_grabopts()
                text = repo.id + '/gpgkeysig'
                sigfile = urlgrabber.urlopen(url, failure_callback=cb, **opts)

            except urlgrabber.grabber.URLGrabError, e:
                sigfile = None

            if sigfile:
                if not misc.valid_detached_sig(sigfile, 
                                    StringIO.StringIO(rawkey), repo.gpgcadir):
                    #if we decide we want to check, even though the sig failed
                    # here is where we would do that
                    raise Errors.YumBaseError(_('GPG key signature on key %s does not match CA Key for repo: %s') % (url, repo.id))
                else:
                    msg = _('GPG key signature verified against CA Key(s)')
                    self.verbose_logger.log(logginglevels.INFO_2, msg)
                    valid_sig = True
            
        # Parse the key
        try:
            keys_info = misc.getgpgkeyinfo(rawkey, multiple=True)
        except ValueError, e:
            raise Errors.YumBaseError(_('Invalid GPG Key from %s: %s') % 
                                      (url, exception2msg(e)))
        keys = []
        for keyinfo in keys_info:
            thiskey = {}
            for info in ('keyid', 'timestamp', 'userid', 
                         'fingerprint', 'raw_key'):
                if info not in keyinfo:
                    raise Errors.YumBaseError, \
                      _('GPG key parsing failed: key does not have value %s') + info
                thiskey[info] = keyinfo[info]
            thiskey['hexkeyid'] = misc.keyIdToRPMVer(keyinfo['keyid']).upper()
            thiskey['valid_sig'] = valid_sig
            thiskey['has_sig'] = bool(sigfile)
            keys.append(thiskey)
        
        return keys

    def _getKeyImportMessage(self, info, keyurl, keytype='GPG'):
        msg = None
        if keyurl.startswith("file:"):
            fname = keyurl[len("file:"):]
            pkgs = self.rpmdb.searchFiles(fname)
            if pkgs:
                pkgs = sorted(pkgs)[-1]
                msg = (_('Importing %s key 0x%s:\n'
                         ' Userid     : "%s"\n'
                         ' Fingerprint: %s\n'
                         ' Package    : %s (%s)\n'
                         ' From       : %s') %
                       (keytype, info['hexkeyid'], to_unicode(info['userid']),
                        misc.gpgkey_fingerprint_ascii(info),
                        pkgs, pkgs.ui_from_repo,
                        keyurl.replace("file://","")))
        if msg is None:
            msg = (_('Importing %s key 0x%s:\n'
                     ' Userid     : "%s"\n'
                     ' Fingerprint: %s\n'
                     ' From       : %s') %
                   (keytype, info['hexkeyid'], to_unicode(info['userid']),
                    misc.gpgkey_fingerprint_ascii(info),
                    keyurl.replace("file://","")))
        self.logger.critical("%s", msg)

    def getKeyForPackage(self, po, askcb = None, fullaskcb = None):
        """Retrieve a key for a package. If needed, use the given
        callback to prompt whether the key should be imported.

        :param po: the package object to retrieve the key of
        :param askcb: Callback function to use to ask permission to
           import a key.  The arguments *askck* should take are the
           package object, the userid of the key, and the keyid
        :param fullaskcb: Callback function to use to ask permission to
           import a key.  This differs from *askcb* in that it gets
           passed a dictionary so that we can expand the values passed.
        :raises: :class:`yum.Errors.YumBaseError` if there are errors
           retrieving the keys
        """
        repo = self.repos.getRepo(po.repoid)
        keyurls = repo.gpgkey
        key_installed = False

        def _prov_key_data(msg):
            msg += _('\n\n\n'
                     ' Failing package is: %s\n'
                     ' GPG Keys are configured as: %s\n'
                     ) % (po, ", ".join(repo.gpgkey))
            return msg

        user_cb_fail = False
        for keyurl in keyurls:
            keys = self._retrievePublicKey(keyurl, repo)

            for info in keys:
                ts = self.rpmdb.readOnlyTS()
                # Check if key is already installed
                if misc.keyInstalled(ts, info['keyid'], info['timestamp']) >= 0:
                    self.logger.info(_('GPG key at %s (0x%s) is already installed') % (
                        keyurl, info['hexkeyid']))
                    continue
                
                if repo.gpgcakey and info['has_sig'] and info['valid_sig']:
                    key_installed = True
                else:
                    # Try installing/updating GPG key
                    self._getKeyImportMessage(info, keyurl)
                    rc = False
                    if self.conf.assumeno:
                        rc = False
                    elif self.conf.assumeyes:
                        rc = True
                        
                    # grab the .sig/.asc for the keyurl, if it exists
                    # if it does check the signature on the key
                    # if it is signed by one of our ca-keys for this repo or the global one
                    # then rc = True
                    # else ask as normal.

                    elif fullaskcb:
                        rc = fullaskcb({"po": po, "userid": info['userid'],
                                        "hexkeyid": info['hexkeyid'], 
                                        "keyurl": keyurl,
                                        "fingerprint": info['fingerprint'],
                                        "timestamp": info['timestamp']})
                    elif askcb:
                        rc = askcb(po, info['userid'], info['hexkeyid'])

                    if not rc:
                        user_cb_fail = True
                        continue
                    
                # Import the key
                ts = self.rpmdb.readOnlyTS()
                result = ts.pgpImportPubkey(misc.procgpgkey(info['raw_key']))
                if result != 0:
                    msg = _('Key import failed (code %d)') % result
                    raise Errors.YumBaseError, _prov_key_data(msg)
                self.logger.info(_('Key imported successfully'))
                key_installed = True

        if not key_installed and user_cb_fail:
            raise Errors.YumBaseError, _("Didn't install any keys")

        if not key_installed:
            msg = _('The GPG keys listed for the "%s" repository are ' \
                  'already installed but they are not correct for this ' \
                  'package.\n' \
                  'Check that the correct key URLs are configured for ' \
                  'this repository.') % repo.name
            raise Errors.YumBaseError, _prov_key_data(msg)

        # Check if the newly installed keys helped
        result, errmsg = self.sigCheckPkg(po)
        if result != 0:
            msg = _("Import of key(s) didn't help, wrong key(s)?")
            self.logger.info(msg)
            errmsg = to_unicode(errmsg)
            raise Errors.YumBaseError, _prov_key_data(errmsg)
    
    def _getAnyKeyForRepo(self, repo, destdir, keyurl_list, is_cakey=False, callback=None):
        """
        Retrieve a key for a repository If needed, prompt for if the key should
        be imported using callback
        
        @param repo: Repository object to retrieve the key of.
        @param destdir: destination of the gpg pub ring
        @param keyurl_list: list of urls for gpg keys
        @param is_cakey: bool - are we pulling in a ca key or not
        @param callback: Callback function to use for asking for permission to
                         import a key. This is verification, but also "choice".
                         Takes a dictionary of key info.
        """

        key_installed = False

        def _prov_key_data(msg):
            cakeytxt = _("No")
            if is_cakey:
                cakeytxt = _("Yes")
            msg += _('\n\n\n'
                     ' CA Key: %s\n'
                     ' Failing repo is: %s\n'
                     ' GPG Keys are configured as: %s\n'
                     ) % (cakeytxt, repo, ", ".join(keyurl_list))
            return msg

        user_cb_fail = False
        for keyurl in keyurl_list:
            keys = self._retrievePublicKey(keyurl, repo, getSig=not is_cakey)
            for info in keys:
                # Check if key is already installed
                if hex(int(info['keyid']))[2:-1].upper() in misc.return_keyids_from_pubring(destdir):
                    self.logger.info(_('GPG key at %s (0x%s) is already imported') % (
                        keyurl, info['hexkeyid']))
                    key_installed = True
                    continue
                # Try installing/updating GPG key
                if is_cakey:
                    # know where the 'imported_cakeys' file is
                    ikf = repo.base_persistdir + '/imported_cakeys'
                    keytype = 'CA'
                    cakeys  = []
                    try:
                        cakeys_d = open(ikf, 'r').read()
                        cakeys = cakeys_d.split('\n')
                    except (IOError, OSError):
                        pass
                    if str(info['hexkeyid']) in cakeys:
                        key_installed = True
                else:
                    keytype = 'GPG'
                    if repo.gpgcakey and info['has_sig'] and info['valid_sig']:
                        key_installed = True
                        
                if not key_installed:
                    self._getKeyImportMessage(info, keyurl, keytype)
                    rc = False
                    if self.conf.assumeno:
                        rc = False
                    elif self.conf.assumeyes:
                        rc = True

                    elif callback:
                        rc = callback({"repo": repo, "userid": info['userid'],
                                        "hexkeyid": info['hexkeyid'], "keyurl": keyurl,
                                        "fingerprint": info['fingerprint'],
                                        "timestamp": info['timestamp']})


                    if not rc:
                        user_cb_fail = True
                        continue
                
                # Import the key
                result = misc.import_key_to_pubring(info['raw_key'], info['hexkeyid'], gpgdir=destdir)
                if not result:
                    msg = _('Key %s import failed') % info['hexkeyid']
                    raise Errors.YumBaseError, _prov_key_data(msg)
                self.logger.info(_('Key imported successfully'))
                key_installed = True
                # write out the key id to imported_cakeys in the repos basedir
                if is_cakey and key_installed:
                    if info['hexkeyid'] not in cakeys:
                        ikfo = open(ikf, 'a')
                        try:
                            ikfo.write(info['hexkeyid']+'\n')
                            ikfo.flush()
                            ikfo.close()
                        except (IOError, OSError):
                            # maybe a warning - but in general this is not-critical, just annoying to the user
                            pass

        if not key_installed and user_cb_fail:
            msg = _("Didn't install any keys for repo %s") % repo
            raise Errors.YumBaseError, _prov_key_data(msg)

        if not key_installed:
            msg = \
                  _('The GPG keys listed for the "%s" repository are ' \
                  'already installed but they are not correct.\n' \
                  'Check that the correct key URLs are configured for ' \
                  'this repository.') % (repo.name)
            raise Errors.YumBaseError, _prov_key_data(msg)

    def getKeyForRepo(self, repo, callback=None):
        """Retrieve a key for a repository.  If needed, use the given
        callback to prompt whether the key should be imported.

        :param repo: repository object to retrieve the key of
        :param callback: callback function to use for asking for
           verification of key information
        """
        self._getAnyKeyForRepo(repo, repo.gpgdir, repo.gpgkey, is_cakey=False, callback=callback)

    def getCAKeyForRepo(self, repo, callback=None):
        """Retrieve a key for a repository.  If needed, use the given
        callback to prompt whether the key should be imported.

        :param repo: repository object to retrieve the key of
        :param callback: callback function to use for asking for
           verification of key information
        """
        self._getAnyKeyForRepo(repo, repo.gpgcadir, repo.gpgcakey, is_cakey=True, callback=callback)

    def _limit_installonly_pkgs(self):
        """ Limit packages based on conf.installonly_limit, if any of the
            packages being installed have a provide in conf.installonlypkgs.
            New in 3.2.24: Obey yumdb_info.installonly data. """

        def _sort_and_filter_installonly(pkgs):
            """ Allow the admin to specify some overrides for installonly pkgs.
                using the yumdb. """
            ret_beg = []
            ret_mid = []
            ret_end = []
            for pkg in sorted(pkgs):
                if 'installonly' not in pkg.yumdb_info:
                    ret_mid.append(pkg)
                    continue

                if pkg.yumdb_info.installonly == 'keep':
                    continue

                if True: # Don't to magic sorting, yet
                    ret_mid.append(pkg)
                    continue

                if pkg.yumdb_info.installonly == 'remove-first':
                    ret_beg.append(pkg)
                elif pkg.yumdb_info.installonly == 'remove-last':
                    ret_end.append(pkg)
                else:
                    ret_mid.append(pkg)

            return ret_beg + ret_mid + ret_end

        if self.conf.installonly_limit < 1 :
            return 

        # We shouldn't alter the transaction if this is set...
        if self.tsInfo._check_future_rpmdbv:
            return
            
        toremove = []
        #  We "probably" want to use either self.ts or self.rpmdb.ts if either
        # is available. However each ts takes a ref. on signals generally, and
        # SIGINT specifically, so we _must_ have got rid of all of the used tses
        # before we try downloading. This is called from buildTransaction()
        # so self.rpmdb.ts should be valid.
        ts = self.rpmdb.readOnlyTS()
        (cur_kernel_v, cur_kernel_r) = misc.get_running_kernel_version_release(ts)
        found = {}
        for m in self.tsInfo.getMembers():
            if m.ts_state not in ('i', 'u'):
                continue
            if m.reinstall:
                continue
            if not self.allowedMultipleInstalls(m.po):
                continue

            if m.name not in found:
                found[m.name] = [m.po]
            else:
                found[m.name].append(m.po)

        for name in found:
            installed = self.rpmdb.searchNevra(name=name)
            installed = _sort_and_filter_installonly(installed)

            total = len(installed) + len(found[name])
            if total <= self.conf.installonly_limit:
                continue # Not adding enough to trigger.

            # Number left to try and remove...
            numleft = total - self.conf.installonly_limit
            for po in installed:
                if (po.version, po.release) == (cur_kernel_v, cur_kernel_r): 
                    # don't remove running
                    continue
                if numleft == 0:
                    break
                toremove.append((po, found[name]))
                numleft -= 1
                        
        for po, newpos in toremove:
            txmbr = self.tsInfo.addErase(po)
            # Add a dep relation to the new version of the package that causes
            # this one to be erased.  This way skipbroken should drop the old
            # one from the transaction if the new one is skipped.  Note that we
            # can only do this for one new version, as skipbroken won't drop
            # deps that are shared with some other packages.  For consistency,
            # let's give up completely if we are installing multiple new
            # versions (which is rather uncommon anyway).
            if len(newpos) == 1:
                txmbr.depends_on.append(newpos[0])

    def processTransaction(self, callback=None,rpmTestDisplay=None, rpmDisplay=None):
        """Process the current transaction.  This involves the
        following steps:
          - Download the packages
          - Check the GPG signatures of the packages
          - Run the test RPM transaction
          - Run the RPM Transaction
        The *callback*.event method is called at the start, and
        between each step.

        :param callback: a callback object, which must have an event
           method
        :param rpmTestDisplay: name of the display class to use in the
           RPM test transaction
        :param rpmDisplay: name of the display class to use in the rpm
           transaction
        """
        
        if not callback:
            callback = callbacks.ProcessTransNoOutputCallback()
        
        # Download Packages
        callback.event(callbacks.PT_DOWNLOAD)
        pkgs = self._downloadPackages(callback)
        # Check Package Signatures
        if pkgs != None:
            callback.event(callbacks.PT_GPGCHECK)
            self._checkSignatures(pkgs,callback)
        # Run Test Transaction
        callback.event(callbacks.PT_TEST_TRANS)
        self._doTestTransaction(callback,display=rpmTestDisplay)
        # Run Transaction
        callback.event(callbacks.PT_TRANSACTION)
        self._doTransaction(callback,display=rpmDisplay)
    
    def _downloadPackages(self,callback):
        ''' Download the need packages in the Transaction '''
        # This can be overloaded by a subclass.    
        dlpkgs = map(lambda x: x.po, filter(lambda txmbr:
                                            txmbr.ts_state in ("i", "u"),
                                            self.tsInfo.getMembers()))
        # Check if there is something to do
        if len(dlpkgs) == 0:
            return None
        # make callback with packages to download                                    
        callback.event(callbacks.PT_DOWNLOAD_PKGS,dlpkgs)
        try:
            probs = self.downloadPkgs(dlpkgs)

        except IndexError:
            raise Errors.YumBaseError, [_("Unable to find a suitable mirror.")]
        if len(probs) > 0:
            errstr = [_("Errors were encountered while downloading packages.")]
            for key in probs:
                errors = misc.unique(probs[key])
                for error in errors:
                    errstr.append("%s: %s" % (key, error))

            raise Errors.YumDownloadError, errstr
        return dlpkgs

    def _checkSignatures(self,pkgs,callback):
        ''' The the signatures of the downloaded packages '''
        # This can be overloaded by a subclass.    
        for po in pkgs:
            result, errmsg = self.sigCheckPkg(po)
            if result == 0:
                # Verified ok, or verify not req'd
                continue            
            elif result == 1:
                self.getKeyForPackage(po, self._askForGPGKeyImport)
            else:
                raise Errors.YumGPGCheckError, errmsg

        return 0
        
    def _askForGPGKeyImport(self, po, userid, hexkeyid):
        ''' 
        Ask for GPGKeyImport 
        This need to be overloaded in a subclass to make GPG Key import work
        '''
        return False
    
    def _doTestTransaction(self,callback,display=None):
        ''' Do the RPM test transaction '''
        self.initActionTs()
        # save our dsCallback out
        dscb = self.dsCallback
        self.dsCallback = None # dumb, dumb dumb dumb!
        self.populateTs( keepold=0 ) # sigh

        # This can be overloaded by a subclass.    
        self.verbose_logger.log(logginglevels.INFO_2, 
                 _('Running Transaction Check'))
        msgs = self._run_rpm_check()
        if msgs:
            rpmlib_only = True
            for msg in msgs:
                if msg.startswith('rpmlib('):
                    continue
                rpmlib_only = False
            if rpmlib_only:
                retmsgs = [_("ERROR You need to update rpm to handle:")]
                retmsgs.extend(msgs)
                raise Errors.YumRPMCheckError, retmsgs
            retmsgs = [_('ERROR with transaction check vs depsolve:')]
            retmsgs.extend(msgs) 
            # Don't encourage users to file a bug here, as this is probably
            # pre-existing issue in dependendies of installed packages
            raise Errors.YumRPMCheckError,retmsgs
        
        tsConf = {}
        for feature in ['diskspacecheck']: # more to come, I'm sure
            tsConf[feature] = getattr( self.conf, feature )
        #
        testcb = RPMTransaction(self, test=True)
        # overwrite the default display class
        if display:
            testcb.display = display
  
        tserrors = self.ts.test( testcb, conf=tsConf )
        del testcb
  
        if len( tserrors ) > 0:
            errstring =  _('Test Transaction Errors: ')
            for descr in tserrors:
                errstring += '  %s\n' % descr 
            raise Errors.YumTestTransactionError, errstring

        del self.ts
        # put back our depcheck callback
        self.dsCallback = dscb


    def _doTransaction(self,callback,display=None):
        ''' do the RPM Transaction '''
        # This can be overloaded by a subclass.    
        self.initActionTs() # make a new, blank ts to populate
        self.populateTs( keepold=0 ) # populate the ts
        self.ts.check() # required for ordering
        self.ts.order() # order
        cb = RPMTransaction(self,display=SimpleCliCallBack)
        # overwrite the default display class
        if display:
            cb.display = display
        self.runTransaction( cb=cb )

    def _run_rpm_check(self):
        results = []
        self.ts.check()
        for prob in self.ts.problems():
            #  Newer rpm (4.8.0+) has problem objects, older have just strings.
            #  Should probably move to using the new objects, when we can. For
            # now just be compatible.
            results.append(to_str(prob))

        return results

    def add_enable_repo(self, repoid, baseurls=[], mirrorlist=None, **kwargs):
        """Add and enable a repository.

        :param repoid: a string specifying the name of the repository
        :param baseurls: a list of strings specifying the urls for
           the repository.  At least one base url, or one mirror, must
           be given
        :param mirrorlist: a list of strings specifying a list of
           mirrors for the repository.  At least one base url, or one
           mirror must be given
        :param kwargs: key word arguments to set any normal repository
           attribute
        :return: the new repository that has been added and enabled
        """
        # out of place fixme - maybe we should make this the default repo addition
        # routine and use it from getReposFromConfigFile(), etc.
        newrepo = yumRepo.YumRepository(repoid)
        newrepo.name = repoid
        newrepo.basecachedir = self.conf.cachedir

        var_convert = kwargs.get('variable_convert', True)
        
        if baseurls:
            replaced = []
            if var_convert:
                for baseurl in baseurls:
                    if baseurl:
                        replaced.append(varReplace(baseurl, self.conf.yumvar))
            else:
                replaced = baseurls
            newrepo.baseurl = replaced

        if mirrorlist:
            if var_convert:
                mirrorlist = varReplace(mirrorlist, self.conf.yumvar)
            newrepo.mirrorlist = mirrorlist

        # setup the repo
        newrepo.setup(cache=self.conf.cache)

        # some reasonable defaults, (imo)
        newrepo.enablegroups = True
        newrepo.metadata_expire = 0
        newrepo.gpgcheck = self.conf.gpgcheck
        newrepo.repo_gpgcheck = self.conf.repo_gpgcheck
        newrepo.basecachedir = self.conf.cachedir
        newrepo.base_persistdir = self.conf._repos_persistdir

        for key in kwargs.keys():
            if not hasattr(newrepo, key): continue # skip the ones which aren't vars
            setattr(newrepo, key, kwargs[key])
        
        # add the new repo
        self.repos.add(newrepo)
        # enable the main repo  
        self.repos.enableRepo(newrepo.id)
        return newrepo

    def setCacheDir(self, force=False, tmpdir=None, reuse=True,
                    suffix='/$basearch/$releasever'):
        """Set a new cache directory.

        :param force: whether to force the cache directory to be
           changed
        :param tmpdir: a temporary directory
        :param reuse: whether the temporary directory can be reused
        :param suffix: suffix to attach to the directory name
        :return: whether the new cache directory is successfully set
        """
        if not force and os.geteuid() == 0:
            return True # We are root, not forced, so happy with the global dir.
        if tmpdir is None:
            tmpdir = os.getenv('TMPDIR')
        if tmpdir is None: # Note that TMPDIR isn't exported by default :(
            tmpdir = '/var/tmp'
        try:
            cachedir = misc.getCacheDir(tmpdir, reuse)
        except (IOError, OSError), e:
            self.logger.critical(_('Could not set cachedir: %s') % exception2msg(e))
            cachedir = None
            
        if cachedir is None:
            return False # Tried, but failed, to get a "user" cachedir

        cachedir += varReplace(suffix, self.conf.yumvar)
        if hasattr(self, 'prerepoconf'):
            self.prerepoconf.cachedir = cachedir
        else:
            self.repos.setCacheDir(cachedir)
        if not hasattr(self, '_old_cachedir'):
            self._old_cachedir = self.conf.cachedir
        self.conf.cachedir = cachedir
        return True # We got a new cache dir

    def _does_this_update(self, pkg1, pkg2):
        """returns True if pkg1 can update pkg2, False if not. 
           This only checks if it can be an update it does not check if
           it is obsoleting or anything else."""
        
        if pkg1.name != pkg2.name:
            return False
        if pkg1.verLE(pkg2):
            return False
        if pkg1.arch not in self.arch.archlist:
            return False
        if rpmUtils.arch.canCoinstall(pkg1.arch, pkg2.arch):
            return False
        if self.allowedMultipleInstalls(pkg1):
            return False
            
        return True    

    def _store_config_in_history(self):
        self.history.write_addon_data('config-main', self.conf.dump())
        myrepos = ''
        for repo in self.repos.listEnabled():
            myrepos += repo.dump()
            myrepos += '\n'
        self.history.write_addon_data('config-repos', myrepos)
        
    def verify_plugins_cb(self, verify_package):
        """Callback to call a plugin hook for pkg.verify().

        :param verify_package: a conduit for the callback
        :return: *verify_package*
        """
        self.plugins.run('verify_package', verify_package=verify_package)
        return verify_package

    def save_ts(self, filename=None, auto=False):
        """Save out a transaction to a .yumtx file to be loaded later.

        :param filename: the name of the file to save the transaction
           in.  If *filename* is not given, a name will be generated
        :param auto: whether to output errors to the logger, rather
           than raising exceptions
        :raises: :class:`yum.Errors.YumBaseError` if there are errors
           saving the transaction
        """
        if self.tsInfo._unresolvedMembers:
            if auto:
                self.logger.critical(_("Dependencies not solved. Will not save unresolved transaction."))
                return
            raise Errors.YumBaseError(_("Dependencies not solved. Will not save unresolved transaction."))
        
        if not filename:
            prefix = 'yum_save_tx.%s' % time.strftime('%Y-%m-%d.%H-%M.')
            fd,filename = tempfile.mkstemp(suffix='.yumtx', prefix=prefix)
            f = os.fdopen(fd, 'w')
        else:
            f = open(filename, 'w')
        
        self._ts_save_file = filename
        
        msg = ["%s\n" % self.rpmdb.simpleVersion(main_only=True)[0],
               "%s\n" % self.ts.getTsFlags()]

        if self.tsInfo._pkgSack is None: # Transactions have pkgSack?
            msg += ["1\n"]
        else:
            msg += ["%s\n" % (len(self.repos.listEnabled()) + 1)]
            for r in self.repos.listEnabled():
                msg += ["%s:%s:%s\n" % (r.id, len(r.sack), r.repoXML.revision)]

        # Save what we think the future rpmdbv will be.
        msg += ["%s:%s\n" % ('installed', self.tsInfo.futureRpmDBVersion())]

        msg += ["%s\n" % len(self.tsInfo.getMembers())]
        for txmbr in self.tsInfo.getMembers():
            msg += [txmbr._dump()]
        try:
            f.write(''.join(msg))
            f.close()
        except (IOError, OSError), e:
            self._ts_save_file = None
            if auto:
                self.logger.critical(_("Could not save transaction file %s: %s") % (filename, exception2msg(e)))
            else:
                raise Errors.YumBaseError(_("Could not save transaction file %s: %s") % (filename, exception2msg(e)))

    def _load_ts_data(self, filename):
        """ Load the file into a simple data format. """
        try:
            data = open(filename, 'r').readlines()
        except (IOError, OSError), e:
            return (exception2msg(e), None)

        if not data:
            return (_("File is empty."), None)

        if data[0] == 'saved_tx:\n':
            #  Old versions of yum would put "saved_tx:" at the beginning and
            # two blank lines at the end when you used:
            # "yum -q history addon-info saved_tx".
            if data[-1] == 'history addon-info\n':
                # Might as well also DTRT if they hand removed the plugins line
                data = data[1:-3]
            else:
                data = data[1:-2]

        return (None, data)
        
    def load_ts(self, filename, ignorerpm=None, ignoremissing=None,
                ignorenewrpm=None):
        """Load a transaction from a .yumtx file.

        :param filename: the name of the file to load the transaction
           from
        :param ignorerpm: whether to ignore starting rpmdb version mismatch.
        :param ignoremissing: whether to ignore that there may be
           transaction members missing
        :param ignorenewrpm: whether to ignore ending rpmdb version mismatch.
        :return: the members of the loaded transaction
        :raises: :class:`yum.Errors.YumBaseError` if there are problems
           loading the transaction
        """
        # check rpmversion - if not match throw a fit
        # check repoversions  (and repos)- if not match throw a fit
        # load each txmbr - if pkgs being updated don't exist, bail w/error
        # setup any ts flags
        # setup cmds for history/yumdb to know about
        # return txmbrs loaded

        data = self._load_ts_data(filename)
        if data[0] is not None:
            raise Errors.YumBaseError(_("Could not access/read saved transaction %s : %s") % (filename, data[0]))
        data = data[1]

        if ignorerpm is None:
            ignorerpm = self.conf.loadts_ignorerpm
        if ignorenewrpm is None:
            ignorenewrpm = self.conf.loadts_ignorenewrpm
        if ignoremissing is None:
            ignoremissing = self.conf.loadts_ignoremissing

        #  Inherit this, because for the ending version to match the starting
        # version must match.
        if ignorerpm:
            ignorenewrpm = True
            
        # data format
        # 0 == rpmdb version
        # 1 == tsflags
        # 2 == numrepos
        # 3:numrepos = repos
        #  -- post 3.2.29 update: 'installed' repo. added with the values as the
        #                         new rpmdb version.
        # 3+numrepos = num pkgs
        # 3+numrepos+1 -> EOF= txmembers

        # rpm db ver
        rpmv = data[0].strip()
        if rpmv != str(self.rpmdb.simpleVersion(main_only=True)[0]):
            msg = _("rpmdb ver mismatched saved transaction version,")
            if ignorerpm:
                msg += _(" ignoring, as requested.")
                self.logger.critical(_(msg))
            else:
                msg += _(" aborting.")
                raise Errors.YumBaseError(msg)
        
        # tsflags
        # FIXME - probably should let other tsflags play nicely together
        #         so someone can add --nogpgcheck or --nodocs or --nodiskspace or some nonsense and have it work
        try:
            tsflags = int(data[1].strip())
        except (ValueError, IndexError), e:
            msg = _("cannot find tsflags or tsflags not integer.")
            raise Errors.YumBaseError(msg)

        self.ts.setFlags(tsflags)
        
        # repos
        numrepos = int(data[2].strip())
        repos = []
        rindex=3+numrepos
        future_rpmdbv = None
        for r in data[3:rindex]:
            repo = r.strip().split(':')

            if repo and repo[0] == 'installed':
                #  This is an update hack to list the _future_ rpmdb version.
                # Doing it this way allows older yum's to load newer ts files.
                future_rpmdbv = "%s:%s" % (repo[1], repo[2])
                continue

            repos.append(repo)

        # pkgs/txmbrs
        numpkgs = int(data[rindex].strip())

        pkgstart = rindex + 1
        
        pkgcount = 0
        pkgprob = False
        curpkg = None
        missingany = False
        for l in data[pkgstart:]:
            l = l.rstrip()
            # our main txmbrs
            if l.startswith('mbr:'):
                if curpkg:
                    self.tsInfo.add(curpkg)
                    if curpkg in self.tsInfo._unresolvedMembers and not missingany:
                        self.tsInfo._unresolvedMembers.remove(curpkg)

                missingany = False
                pkgtup, current_state = l.split(':')[1].strip().split(' ')
                current_state = int(current_state.strip())
                pkgtup = tuple(pkgtup.strip().split(','))
                try:
                    if current_state == TS_INSTALL:
                        po = self.getInstalledPackageObject(pkgtup)
                    elif current_state == TS_AVAILABLE:
                        po = self.getPackageObject(pkgtup)
                    else:
                        msg = _("Found txmbr in unknown current state: %s" % current_state)
                        raise Errors.YumBaseError(msg)
                except Errors.YumBaseError, e:
                    missingany = True
                    msg = _("Could not find txmbr: %s in state %s" % (str(pkgtup), current_state))
                    if not ignoremissing:
                        raise Errors.YumBaseError(msg)
                    else:
                        ignorenewrpm = True
                        self.logger.critical(msg)
                else:
                    pkgcount += 1
                    curpkg = transactioninfo.TransactionMember(po)
                    curpkg.current_state = current_state
                    continue

            l = l.strip()
            k,v = l.split(':', 1)
            v = v.lstrip()
            # attributes of our txmbrs
            if k in ('isDep', 'reinstall'):
                v = v.strip().lower()
                if v == 'false':
                    setattr(curpkg, k, False)
                elif v == 'true':
                    setattr(curpkg, k, True)
            elif k in ('output_state'):
                setattr(curpkg, k, int(v.strip()))
            elif k in ('groups'):
                curpkg.groups.extend(v.split(' '))
            # the relationships to our main txmbrs
            elif k in ('updated_by', 'obsoleted_by', 'downgraded_by', 
                       'downgrades', 'updates', 'obsoletes', 'depends_on'):
                for pkgspec in v.strip().split(' '):
                    pkgtup, origin  = pkgspec.split('@')
                    try:
                        if origin == 'i':
                            po = self.getInstalledPackageObject(tuple(pkgtup.split(',')))
                        else:
                            po = self.getPackageObject(tuple(pkgtup.split(',')))
                    except Errors.YumBaseError, e:
                        msg = _("Could not find txmbr: %s from origin: %s" % (str(pkgtup), origin))
                        self.logger.critical(msg)
                        missingany = True
                    else:
                        curlist = getattr(curpkg, k)
                        curlist.append(po)
                        setattr(curpkg, k, curlist)
            elif k in ('relatedto'):
                for item in v.split(' '):
                    pkgspec, rel = item.split(':')
                    pkgtup,origin = pkgspec.split('@')
                    try:
                        if origin == 'i':
                            po = self.getInstalledPackageObject(tuple(pkgtup.split(',')))
                        else:
                            po = self.getPackageObject(tuple(pkgtup.split(',')))
                    except Errors.YumBaseError, e:
                        msg = _("Could not find txmbr: %s from origin: %s" % (str(pkgtup), origin))
                        self.logger.critical(msg)
                        missingany = True
                    else:
                        curlist = getattr(curpkg, k)
                        curlist.append((po,rel))
                        setattr(curpkg, k, curlist)
                        
            # the plain strings
            else: #ts_state, reason
                setattr(curpkg, k, v.strip())
            
            if missingany:
                pkgprob = True
                
        # make sure we get the last one in!
        self.tsInfo.add(curpkg)
        if curpkg in self.tsInfo._unresolvedMembers:
            self.tsInfo._unresolvedMembers.remove(curpkg)

            
        if numpkgs != pkgcount:
            pkgprob = True
            
        if pkgprob:
            msg = _("Transaction members, relations are missing or ts has been modified,")
            if ignoremissing:
                ignorenewrpm = True
                msg += _(" ignoring, as requested. You must redepsolve!")
                self.logger.critical(msg)
            else:
                msg += _(" aborting.")
                raise Errors.YumBaseError(msg)
            
        if len(self.tsInfo) != pkgcount:
            future_rpmdbv = None
        if future_rpmdbv is not None:
            self.tsInfo._check_future_rpmdbv = (pkgcount, future_rpmdbv,
                                                ignorenewrpm)
        return self.tsInfo.getMembers()

    def _remove_old_deps(self):
        """take the set of pkgs being removed and remove any pkgs which are:
           1. not required anymore
           2. marked as a 'dep' in the 'reason' in the yumdb. """
        found_leaves = set()
        checked = set()
        beingremoved = [ t.po for t in self.tsInfo.getMembersWithState(output_states=TS_REMOVE_STATES) ]
        # cache previously examined packages
        okay_to_remove = {}
        for i in self.rpmdb.returnPackages():
            okay_to_remove[i] = True

        for pkg in beingremoved: # for each package required by the pkg being removed
            #print 'removal: %s' % pkg.name
            for required in pkg.required_packages():
                #if required in checked:
                #    continue # if we've already checked it, skip it.
                #checked.add(required)
                if required.yumdb_info.get('reason', '') != 'dep': # if the required pkg is not a dep, then skip it
                    okay_to_remove[required] = False
                    continue
                if required in beingremoved:
                    continue
                if self._has_needed_revdeps(required, beingremoved, okay_to_remove):
                    continue
                still_needed = False
                for requiring in required.requiring_packages(): # so we have required deps - look at all the pkgs which require them
                    if requiring == required: # if they are self-requiring skip them
                        continue
                        
                    #for tbi_pkg in self.tsInfo.getMembersWithState(output_states=TS_INSTALL_STATES):
                    #   for reqtuple in tbi_pkg.po.requires:
                    #        if required.provides_for(reqtuple):
                    #            still_needed = True
                    #            break
                
                if not still_needed:
                    print '---> Marking %s to be removed - no longer needed by %s' % (required.name, pkg.name)
                    txmbrs = self.remove(po=required)

                    for txmbr in txmbrs:
                        txmbr.setAsDep(po=pkg)
                        if txmbr.po not in beingremoved:
                            beingremoved.append(txmbr.po)
                        found_leaves.add(txmbr)
        self.verbose_logger.log(logginglevels.INFO_2, "Found and removing %s unneeded dependencies" % len(found_leaves))
            
    # Checks if pkg has any reverse deps which cannot be removed. 
    # Currently this only checks the install reason for each revdep, 
    # but we may want to check for other reasons that would  
    # prevent the revdep from being removed (e.g. protected)
    def _has_needed_revdeps(self, pkg, beingremoved, ok_to_remove):
        # check if we've already found this package to have user-installed deps
        if not ok_to_remove[pkg]:
            # Debugging output
            self.verbose_logger.log(logginglevels.DEBUG_2, _("%s has been visited already and cannot be removed."), pkg)
            return True
        # Debugging output
        self.verbose_logger.log(logginglevels.DEBUG_2, _("Examining revdeps of %s"), pkg)
        # track which pkgs we have visited already
        visited = {}
        for po in self.rpmdb.returnPackages():
            visited[po] = False
        # no need to consider packages that are already being removed
        for po in beingremoved:
            visited[po] = True
        stack = []
        stack.append(pkg)
        # depth-first search
        while stack:
            curpkg = stack[-1]
            if not visited[curpkg]:
                if not ok_to_remove[curpkg]:
                    # Debugging output
                    self.verbose_logger.log(logginglevels.DEBUG_2, _("%s has been visited already and cannot be removed."), pkg)
                    ok_to_remove[pkg] = False
                    return True
                if curpkg.yumdb_info.get('reason', '') != 'dep':
                    # Debugging output
                    self.verbose_logger.log(logginglevels.DEBUG_2, _("%s has revdep %s which was user-installed."), pkg, curpkg)
                    ok_to_remove[pkg] = False
                    ok_to_remove[curpkg] = False
                    return True

                #  Go through the stuff in the ts to be installed - make sure
                # none of that needs the required pkg, either.
                for (provn,provf,provevr) in curpkg.provides:
                    if self.tsInfo.getNewRequires(provn, provf, provevr).keys():
                        ok_to_remove[pkg] = False
                        ok_to_remove[curpkg] = False
                        self.verbose_logger.log(logginglevels.DEBUG_2, _("%s is needed by a package to be installed."), curpkg)
                        return True
                for fn in curpkg.filelist + curpkg.dirlist:
                    if self.tsInfo.getNewRequires(fn, None,(None,None,None)).keys():
                        ok_to_remove[pkg] = False
                        ok_to_remove[curpkg] = False
                        self.verbose_logger.log(logginglevels.DEBUG_2, _("%s is needed by a package to be installed."), curpkg)
                        return True

                visited[curpkg] = True
            all_leaves_visited = True
            leaves = curpkg.requiring_packages()
            for leaf in leaves:
                if not visited[leaf]:
                    stack.append(leaf)
                    all_leaves_visited = False
                    break
            if all_leaves_visited:
                stack.pop()
        # Debugging output
        self.verbose_logger.log(logginglevels.DEBUG_2, _("%s has no user-installed revdeps."), pkg)
        return False
