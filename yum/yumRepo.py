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
# Copyright 2005 Duke University
# Copyright 2007 Red Hat
import os
import re
import time
import types
import urlparse
urlparse.uses_fragment.append("media")
import urllib

import Errors
from urlgrabber.grabber import URLGrabber
from urlgrabber.grabber import default_grabber
from urlgrabber.progress import format_number
import urlgrabber.mirror
from urlgrabber.grabber import URLGrabError
import repoMDObject
import packageSack
from repos import Repository
import parser
import sqlitecachec
import sqlitesack
from yum import config
from yum import misc
from yum import comps
from yum import _
from constants import *
import metalink

import logging
import logginglevels

import warnings

import glob
import shutil
import stat
import errno
import tempfile

# This is unused now, probably nothing uses it but it was global/public.
skip_old_DBMD_check = False

try:
    import xattr
    if not hasattr(xattr, 'get') or not hasattr(xattr, 'set'):
        xattr = None # This is a "newer" API.
except ImportError:
    xattr = None

#  The problem we are trying to solve here is that:
#
# 1. We rarely want to be downloading MD/pkgs/etc.
# 2. We want to check those files are valid (match checksums) when we do
#    download them.
# 3. We _really_ don't want to checksum all the files every time we
#    run (100s of MBs).
# 4. We can continue to download files from bad mirrors, or retry files due to
#    C-c etc.
#
# ...we used to solve this by just checking the file size, and assuming the
# files had been downloaded and checksumed as correct if that matched. But that
# was error prone on bad mirrors, so now we store the checksum in an
# xattr ... this does mean that if you can't store xattrs (Eg. NFS) you will
# rechecksum everything constantly.

def _xattr_get_chksum(filename, chktype):
    if not xattr:
        return None

    try:
        ret = xattr.get(filename, 'user.yum.checksum.' + chktype)
    except: # Documented to be "EnvironmentError", but make sure
        return None

    return ret

def _xattr_set_chksum(filename, chktype, chksum):
    if not xattr:
        return None

    try:
        xattr.set(filename, 'user.yum.checksum.' + chktype, chksum)
    except:
        return False # Data too long. = IOError ... ignore everything.

    return True


warnings.simplefilter("ignore", Errors.YumFutureDeprecationWarning)

logger = logging.getLogger("yum.Repos")
verbose_logger = logging.getLogger("yum.verbose.Repos")

class YumPackageSack(packageSack.PackageSack):
    """imports/handles package objects from an mdcache dict object"""
    def __init__(self, packageClass):
        packageSack.PackageSack.__init__(self)
        self.pc = packageClass
        self.added = {}

    def __del__(self):
        self.close()

    def close(self):
        self.added = {}

    def addDict(self, repo, datatype, dataobj, callback=None):
        if repo in self.added:
            if datatype in self.added[repo]:
                return

        total = len(dataobj)
        if datatype == 'metadata':
            current = 0
            for pkgid in dataobj:
                current += 1
                if callback: callback.progressbar(current, total, repo)
                pkgdict = dataobj[pkgid]
                po = self.pc(repo, pkgdict)
                po.id = pkgid
                self._addToDictAsList(self.pkgsByID, pkgid, po)
                self.addPackage(po)

            if repo not in self.added:
                self.added[repo] = []
            self.added[repo].append('metadata')
            # indexes will need to be rebuilt
            self.indexesBuilt = 0

        elif datatype in ['filelists', 'otherdata']:
            if repo in self.added:
                if 'metadata' not in self.added[repo]:
                    raise Errors.RepoError, '%s md for %s imported before primary' \
                           % (datatype, repo.ui_id)
            current = 0
            for pkgid in dataobj:
                current += 1
                if callback: callback.progressbar(current, total, repo)
                pkgdict = dataobj[pkgid]
                if pkgid in self.pkgsByID:
                    for po in self.pkgsByID[pkgid]:
                        po.importFromDict(pkgdict)

            self.added[repo].append(datatype)
            # indexes will need to be rebuilt
            self.indexesBuilt = 0
        else:
            # umm, wtf?
            pass

    def _retrieve_async(self, repo, data):
        """ Just schedule the metadata downloads """

        for item in data:
            if item in self.added.get(repo, []):
                continue
            if item == 'metadata':
                mydbtype = 'primary_db'
            elif item == 'filelists':
                mydbtype = 'filelists_db'
            elif item == 'otherdata':
                mydbtype = 'other_db'
            else:
                continue

            if self._check_db_version(repo, mydbtype):
                if not self._check_uncompressed_db_gen(repo, mydbtype):
                    # NOTE: No failfunc.
                    repo._retrieveMD(mydbtype, async=True, failfunc=None)

    def populate(self, repo, mdtype='metadata', callback=None, cacheonly=0):
        if mdtype == 'all':
            data = ['metadata', 'filelists', 'otherdata']
        else:
            data = [ mdtype ]

        if not hasattr(repo, 'cacheHandler'):
            repo.cacheHandler = sqlitecachec.RepodataParserSqlite(
                storedir=repo.cachedir,
                repoid=repo.id,
                callback=callback,
                )
        for item in data:
            if repo in self.added:
                if item in self.added[repo]:
                    continue

            if item == 'metadata':
                mydbtype = 'primary_db'
                mymdtype = 'primary'
                repo_get_function = repo.getPrimaryXML
                repo_cache_function = repo.cacheHandler.getPrimary

            elif item == 'filelists':
                mydbtype = 'filelists_db'
                mymdtype = 'filelists'
                repo_get_function = repo.getFileListsXML
                repo_cache_function = repo.cacheHandler.getFilelists

            elif item == 'otherdata':
                mydbtype = 'other_db'
                mymdtype = 'other'
                repo_get_function = repo.getOtherXML
                repo_cache_function = repo.cacheHandler.getOtherdata

            else:
                continue

            if self._check_db_version(repo, mydbtype):
                #  Use gen decompression on DB files. Keeps exactly what we
                # downloaded in the download dir.

                # Backwards compat. ... try the old uncompressed version first.
                db_un_fn = self._check_uncompressed_db(repo, mydbtype)
                if not db_un_fn:
                    db_un_fn = self._check_uncompressed_db_gen(repo, mydbtype)

                if not db_un_fn:
                    db_fn = repo._retrieveMD(mydbtype)
                    if db_fn:
                        # unlink the decompressed file, we know it's not valid
                        misc.unlink_f(repo.cachedir +'/gen/%s.sqlite' % mydbtype)
                        db_un_fn = self._check_uncompressed_db_gen(repo,
                                                                   mydbtype)
                    if not db_un_fn: # Shouldn't happen?
                        raise Errors.RepoError, '%s: Check uncompressed DB failed' % repo

                dobj = repo.cacheHandler.open_database(db_un_fn)

            else:
                repo._xml2sqlite_local = True
                # Download...
                xml = repo_get_function()

                #  Use generated dir. and handle compression types metadata
                # parser doesn't understand.
                gen = mymdtype + '.xml'
                ret = misc.repo_gen_decompress(xml, gen, cached=repo.cache)
                if not ret:
                    raise Errors.RepoError, '%s: Decompress DB failed' % repo
                xml = ret
                # Convert XML => .sqlite
                xmldata = repo.repoXML.getData(mymdtype)
                (ctype, csum) = xmldata.checksum
                dobj = repo_cache_function(xml, csum)

            if not cacheonly:
                self.addDict(repo, item, dobj, callback)
            del dobj


        # get rid of all this stuff we don't need now
        del repo.cacheHandler

    def _check_uncompressed_db_gen(self, repo, mdtype):
        """return file name of db in gen/ dir if good, None if not"""

        mydbdata         = repo.repoXML.getData(mdtype)
        (r_base, remote) = mydbdata.location
        fname            = os.path.basename(remote)
        compressed_fn    = repo.cachedir + '/' + fname
        db_un_fn         = mdtype + '.sqlite'

        if not repo._checkMD(compressed_fn, mdtype, data=mydbdata,
                             check_can_fail=True):
            return None

        ret = misc.repo_gen_decompress(compressed_fn, db_un_fn,
                                       cached=repo.cache)
        if ret:
            return self._check_uncompressed_db_fn(repo, mdtype, ret)
        return None

    def _check_uncompressed_db(self, repo, mdtype):
        """return file name of uncompressed db is good, None if not"""
        mydbdata = repo.repoXML.getData(mdtype)
        (r_base, remote) = mydbdata.location
        fname = os.path.basename(remote)
        compressed_fn = repo.cachedir + '/' + fname
        db_un_fn = misc.decompress(compressed_fn, fn_only=True)

        return self._check_uncompressed_db_fn(repo, mdtype, db_un_fn)

    def _check_uncompressed_db_fn(self, repo, mdtype, db_un_fn):
        result = None

        if os.path.exists(db_un_fn):


            try:
                repo.checkMD(db_un_fn, mdtype, openchecksum=True)
            except URLGrabError:
                if not repo.cache:
                    misc.unlink_f(db_un_fn)
            else:
                result = db_un_fn

        return result

    def _check_db_version(self, repo, mdtype):
        return repo._check_db_version(mdtype)

class YumRepository(Repository, config.RepoConf):
    """
    This is an actual repository object

    Configuration attributes are pulled in from config.RepoConf.
    """

    def __init__(self, repoid):
        config.RepoConf.__init__(self)
        Repository.__init__(self, repoid)

        self.repofile = None
        self.mirrorurls = []
        self._urls = []
        self.enablegroups = 0
        self.groupsfilename = 'yumgroups.xml' # something some freaks might
                                              # eventually want
        self.repoMDFile = 'repodata/repomd.xml'
        self._repoXML = None
        self._oldRepoMDData = {}
        self.cache = 0
        self.mirrorlistparsed = 0
        self.yumvar = {} # empty dict of yumvariables for $string replacement
        self._proxy_dict = {}
        self.metadata_cookie_fn = 'cachecookie'
        self._metadataCurrent = None
        self._metalink = None
        self.groups_added = False
        self.http_headers = {}
        self.repo_config_age = 0 # if we're a repo not from a file then the
                                 # config is very, very old
        # throw in some stubs for things that will be set by the config class
        self.basecachedir = ""
        self.base_persistdir = ""
        self.cost = 1000
        self.copy_local = 0
        # holder for stuff we've grabbed
        self.retrieved = { 'primary':0, 'filelists':0, 'other':0, 'group':0,
                           'updateinfo':0, 'prestodelta':0}

        # callbacks
        self.callback = None  # for the grabber
        self.multi_callback = None
        self.failure_obj = None
        self.mirror_failure_obj = None
        self.interrupt_callback = None
        self._callbacks_changed = False

        # callback function for handling media
        self.mediafunc = None

        # callbacks for gpg key importing and confirmation
        self.gpg_import_func = None
        self.gpgca_import_func = None
        self.confirm_func = None

        #  The reason we want to turn this off are things like repoids
        # called "tmp" in repoquery --repofrompath and/or new1/old1 in repodiff.
        self.timestamp_check = True

        self._sack = None

        self._grabfunc = None
        self._grab = None
        self._async = False

    def __cmp__(self, other):
        """ Sort yum repos. by cost, and then by alphanumeric on their id. """
        if other is None:
            return 1
        if hasattr(other, 'cost'):
            ocost = other.cost
        else:
            ocost = 1000
        ret = cmp(self.cost, ocost)
        if ret:
            return ret
        return cmp(self.id, other.id)

    def _getSack(self):
        # FIXME: Note that having the repo hold the sack, which holds "repos"
        # is not only confusing but creates a circular dep.
        #  Atm. we don't leak memory because RepoStorage.close() is called,
        # which calls repo.close() which calls sack.close() which removes the
        # repos from the sack ... thus. breaking the cycle.
        if self._sack is None:
            self._sack = sqlitesack.YumSqlitePackageSack(
                sqlitesack.YumAvailablePackageSqlite)
        return self._sack
    sack = property(_getSack)

    def _ui_id(self):
        """ Show self.id, but include any $releasever/$basearch/etc. data. """
        if hasattr(self, '__cached_ui_id'):
            return getattr(self, '__cached_ui_id')

        val = config._readRawRepoFile(self)
        if not val:
            val = ''
        else:
            ini, section_id = val
            ini = ini[section_id]
            if 'metalink' in ini:
                val = ini['metalink']
            elif 'mirrorlist' in ini:
                val = ini['mirrorlist']
            elif 'baseurl' in ini:
                val = ini['baseurl']
            else:
                val = ''
        ret = self.id

        for var in self.ui_repoid_vars:
            if '$'+var in val:
                ret += '/'
                ret += str(self.yumvar[var])

        setattr(self, '__cached_ui_id', ret)
        return ret
    ui_id = property(_ui_id)

    def close(self):
        if self._sack is not None:
            self.sack.close()
        Repository.close(self)

    def _resetSack(self):
        self._sack = None

    def __getProxyDict(self):
        self.doProxyDict()
        if self._proxy_dict:
            return self._proxy_dict
        return None

    # consistent access to how proxy information should look (and ensuring
    # that it's actually determined for the repo)
    proxy_dict = property(__getProxyDict)

    def getPackageSack(self):
        """Returns the instance of this repository's package sack."""
        return self.sack


    def ready(self):
        """Returns true if this repository is setup and ready for use."""
        if hasattr(self, 'metadata_cookie'):
            return self.repoXML is not None
        return False


    def getGroupLocation(self):
        """Returns the location of the group."""
        if 'group_gz' in self.repoXML.fileTypes():
            thisdata = self.repoXML.getData('group_gz')
        else:
            thisdata = self.repoXML.getData('group')
        return thisdata.location

    def __str__(self):
        # Note: You might expect this to be .ui_id, except people got used to
        # the fact that str(repo) == repo.id and used the former instead of
        # the later when they wanted just the .id. So we have to live with it
        # and use .ui_id explicitly.
        return self.id

    def _checksum(self, sumtype, file, CHUNK=2**16, checksum_can_fail=False,
                  datasize=None):
        """takes filename, hand back Checksum of it
           sumtype = md5 or sha
           filename = /path/to/file
           CHUNK=65536 by default"""
        try:
            return misc.checksum(sumtype, file, CHUNK, datasize)
        except (Errors.MiscError, EnvironmentError), e:
            if checksum_can_fail:
                return None
            raise Errors.RepoError, 'Error opening file for checksum: %s' % e

    def dump(self):
        output = '[%s]\n' % self.id
        # we exclude all vars which start with _ or are in this list:
        excluded_vars = ('mediafunc', 'sack', 'metalink_data', 'grab', 
                         'grabfunc', 'repoXML', 'cfg', 'retrieved',
                        'mirrorlistparsed', 'gpg_import_func', 
                        'gpgca_import_func', 'failure_obj',
                        'callback', 'confirm_func', 'groups_added', 
                        'interrupt_callback', 'id', 'mirror_failure_obj',
                        'repo_config_age', 'groupsfilename', 'copy_local', 
                        'basecachedir', 'http_headers', 'metadata_cookie',
                        'metadata_cookie_fn', 'quick_enable_disable',
                        'repoMDFile', 'timestamp_check', 'urls', 'mirrorurls',
                        'yumvar', 'repofile', 'multi_callback')
        for attr in dir(self):
            if attr.startswith('_'):
                continue
            if attr in excluded_vars:
                continue
            if isinstance(getattr(self, attr), types.MethodType):
                continue
            res = getattr(self, attr)
            if not res and type(res) not in (type(False), type(0)):
                res = ''
            if type(res) == types.ListType:
                res = ',\n   '.join(res)
            output = output + '%s = %s\n' % (attr, res)

        return output

    def enablePersistent(self):
        """Persistently enables this repository."""
        self.enable()
        try:
            config.writeRawRepoFile(self,only=['enabled'])
        except IOError, e:
            if e.errno == errno.EACCES:
                logger.warning(e)
            else:
                raise IOError, str(e)

    def disablePersistent(self):
        """Persistently disables this repository."""
        self.disable()
        try:
            config.writeRawRepoFile(self,only=['enabled'])
        except IOError, e:
            if e.errno == errno.EACCES:
                logger.warning(e)
            else:
                raise IOError, str(e)

    def check(self):
        """self-check the repo information  - if we don't have enough to move
           on then raise a repo error"""
        if len(self._urls) < 1 and not self.mediaid:
            raise Errors.RepoError, \
             'Cannot find a valid baseurl for repo: %s' % self.ui_id

    def doProxyDict(self):
        if self._proxy_dict:
            return

        self._proxy_dict = {} # zap it
        proxy_string = None
        empty = (None, '_none_', '')
        if self.proxy is None:  # got 'proxy=_none_'
            proxy_string = ''   # this disables default proxies
        elif self.proxy:
            proxy_string = '%s' % self.proxy
            if self.proxy_username not in empty:

                auth = urllib.quote(self.proxy_username)
                if self.proxy_password not in empty:
                    auth += ':' + urllib.quote(self.proxy_password)

                proto, rest = re.match('(\w+://)(.+)', proxy_string).groups()
                proxy_string = '%s%s@%s' % (proto, auth, rest)

        if proxy_string is not None:
            self._proxy_dict['http'] = proxy_string
            self._proxy_dict['https'] = proxy_string
            self._proxy_dict['ftp'] = proxy_string

    def __headersListFromDict(self, cache=True):
        """Convert our dict of headers to a list of 2-tuples for urlgrabber."""
        headers = []

        for key in self.http_headers:
            headers.append((key, self.http_headers[key]))
        if not (cache or 'Pragma' in self.http_headers):
            headers.append(('Pragma', 'no-cache'))

        return headers

    def setupGrab(self):
        warnings.warn('setupGrab() will go away in a future version of Yum.\n',
                Errors.YumFutureDeprecationWarning, stacklevel=2)
        self._setupGrab()

    def _setupGrab(self):
        """sets up the grabber functions with the already stocked in urls for
           the mirror groups"""

        if self.failovermethod == 'roundrobin':
            mgclass = urlgrabber.mirror.MGRandomOrder
        else:
            mgclass = urlgrabber.mirror.MirrorGroup

        ugopts = self._default_grabopts()
        self._grabfunc = URLGrabber(progress_obj=self.callback,
                                    multi_progress_obj=self.multi_callback,
                                    failure_callback=self.failure_obj,
                                    interrupt_callback=self.interrupt_callback,
                                    copy_local=self.copy_local,
                                    reget='simple',
                                    **ugopts)
        def add_mc(url):
            host = urlparse.urlsplit(url).netloc.split('@')[-1]
            mc = self.metalink_data._host2mc.get(host)
            if mc:
                url = {
                    'mirror': misc.to_utf8(url),
                    'kwargs': {
                        'max_connections': mc.max_connections,
                        'preference': mc.preference,
                        'private': mc.private,
                    },
                }
            return url
        urls = self.urls
        if self.metalink:
            urls = map(add_mc, urls)

        def mirror_failure(obj):
            action = {}

            # timeout, refused connect, and HTTP 503 may retry
            e = obj.exception
            if e.errno == 12 or \
               e.errno == 14 and getattr(e, 'code', 0) in (7, 503):
                tries = getattr(obj, 'tries', self.retries)
                if tries <= self.retries - len(self.urls):
                    # don't remove this mirror yet
                    action['remove'] = False
            elif e.errno == -3:
                # unsupported checksum type, fail now
                action['fail'] = True

            # No known user of this callback, but just in case...
            cb = self.mirror_failure_obj
            if cb:
                fun, arg, karg = callable(cb) and (cb, (), {}) or cb
                action.update(fun(obj, *arg, **karg))

            return action

        self._grab = mgclass(self._grabfunc, urls,
                             failure_callback=mirror_failure)

    def _default_grabopts(self, cache=True):
        opts = { 'keepalive': self.keepalive,
                 'bandwidth': self.bandwidth,
                 'retry': self.retries,
                 'throttle': self.throttle,
                 'timeout': self.timeout,
                 'minrate': self.minrate,
                 'ip_resolve': self.ip_resolve,
                 'http_headers': tuple(self.__headersListFromDict(cache=cache)),
                 'ssl_verify_peer': self.sslverify,
                 'ssl_verify_host': self.sslverify,
                 'ssl_ca_cert': self.sslcacert,
                 'ssl_cert': self.sslclientcert,
                 'ssl_key': self.sslclientkey,
                 'user_agent': default_grabber.opts.user_agent,
                 'username': self.username,
                 'password': self.password,
                 }
        if self.proxy == 'libproxy':
            opts['libproxy'] = True
        else:
            opts['proxies'] = self.proxy_dict
        return opts

    def _getgrabfunc(self):
        if not self._grabfunc or self._callbacks_changed:
            self._setupGrab()
            self._callbacks_changed = False
        return self._grabfunc

    def _getgrab(self):
        if not self._grab or self._callbacks_changed:
            self._setupGrab()
            self._callbacks_changed = False
        return self._grab

    grabfunc = property(lambda self: self._getgrabfunc())
    grab = property(lambda self: self._getgrab())

    def _dirSetupMkdir_p(self, dpath):
        """make the necessary directory path, if possible, raise on failure"""
        if os.path.exists(dpath) and os.path.isdir(dpath):
            return

        try:
            os.makedirs(dpath, mode=0755)
        except OSError, e:
            msg = "%s: %s %s: %s" % ("Error making cache directory",
                                     dpath, "error was", e)
            raise Errors.RepoError, msg

    def dirSetup(self):
        """make the necessary dirs, if possible, raise on failure"""

        cachedir = os.path.join(self.basecachedir, self.id)
        persistdir = os.path.join(self.base_persistdir, self.id)
        pkgdir = os.path.join(cachedir, 'packages')
        hdrdir = os.path.join(cachedir, 'headers')
        self.setAttribute('_dir_setup_cachedir', cachedir)
        self.setAttribute('_dir_setup_pkgdir', pkgdir)
        self.setAttribute('_dir_setup_hdrdir', hdrdir)
        self.setAttribute('_dir_setup_persistdir', persistdir)
        ext=''
        if os.geteuid() != 0:
            ext = '-ro'
        self.setAttribute('_dir_setup_gpgdir', persistdir + '/gpgdir' + ext)
        self.setAttribute('_dir_setup_gpgcadir', persistdir + '/gpgcadir' + ext)

        cookie = self.cachedir + '/' + self.metadata_cookie_fn
        self.setAttribute('_dir_setup_metadata_cookie', cookie)

        for dir in [self.cachedir, self.cachedir + '/gen', self.pkgdir]:
            self._dirSetupMkdir_p(dir)

        # persistdir is really root-only but try the make anyway and just
        # catch the exception
        for dir in [self.persistdir]:
            try:
                self._dirSetupMkdir_p(dir)
            except Errors.RepoError, e:
                pass
                
        # if we're using a cachedir that's not the system one, copy over these
        # basic items from the system one
        self._preload_md_from_system_cache('repomd.xml')
        self._preload_md_from_system_cache('cachecookie')
        self._preload_md_from_system_cache('mirrorlist.txt')
        self._preload_md_from_system_cache('metalink.xml')

    def _dirGetAttr(self, attr):
        """ Make the directory attributes call .dirSetup() if needed. """
        attr = '_dir_setup_' + attr
        if not hasattr(self, attr):
            self.dirSetup()
        return getattr(self, attr)
    def _dirSetAttr(self, attr, val):
        """ Make the directory attributes call .dirSetup() if needed. """
        attr = '_dir_setup_' + attr
        if not hasattr(self, attr):
            self.dirSetup()

        if attr == '_dir_setup_pkgdir':
            if not hasattr(self, '_old_pkgdirs'):
                self._old_pkgdirs = []
            self._old_pkgdirs.append(getattr(self, attr))

        ret = setattr(self, attr, val)
        if attr in ('_dir_setup_pkgdir', ):
            self._dirSetupMkdir_p(val)
        return ret
    cachedir = property(lambda self: self._dirGetAttr('cachedir'))
    persistdir = property(lambda self: self._dirGetAttr('persistdir'))

    pkgdir   = property(lambda self: self._dirGetAttr('pkgdir'),
                        lambda self, x: self._dirSetAttr('pkgdir', x))
    hdrdir   = property(lambda self: self._dirGetAttr('hdrdir'),
                        lambda self, x: self._dirSetAttr('hdrdir', x))
    gpgdir   = property(lambda self: self._dirGetAttr('gpgdir'),
                        lambda self, x: self._dirSetAttr('gpgdir', x))
    gpgcadir   = property(lambda self: self._dirGetAttr('gpgcadir'),  
                        lambda self, x: self._dirSetAttr('gpgcadir', x))
    metadata_cookie = property(lambda self: self._dirGetAttr('metadata_cookie'))

    def baseurlSetup(self):
        warnings.warn('baseurlSetup() will go away in a future version of Yum.\n',
                Errors.YumFutureDeprecationWarning, stacklevel=2)
        self._baseurlSetup()

    def _hack_mirrorlist_for_anaconda(self):
        #  Anaconda doesn't like having mirrorlist and metalink, so we allow
        # mirrorlist to act like metalink. Except we'd really like to know which
        # we have without parsing it ... and want to store it in the right
        # place etc.
        #  So here is #1 hack: see if the metalin kis unset and the mirrorlist
        # URL contains the string "metalink", if it does we copy it over.
        if self.metalink:
            return
        if not self.mirrorlist:
            return
        if self.mirrorlist.find("metalink") == -1:
            return
        self.metalink = self.mirrorlist

    def _baseurlSetup(self):
        """go through the baseurls and mirrorlists and populate self.urls
           with valid ones, run  self.check() at the end to make sure it worked"""

        self.baseurl = self._replace_and_check_url(self.baseurl)
        # FIXME: We put all the mirrors in .baseurl as well as
        # .urls for backward compat. (see bottom of func). So we'll save this
        # out for repolist -v ... or anything else wants to know the baseurl
        self._orig_baseurl = self.baseurl

        mirrorurls = []
        self._hack_mirrorlist_for_anaconda()
        if self.metalink and not self.mirrorlistparsed:
            # FIXME: This is kind of lying to API callers
            mirrorurls.extend(list(self.metalink_data.urls()))
            self.mirrorlistparsed = True
        if self.mirrorlist and not self.mirrorlistparsed:
            mirrorurls.extend(self._getMirrorList())
            self.mirrorlistparsed = True

        self.mirrorurls = self._replace_and_check_url(mirrorurls)
        self._urls = self.baseurl + self.mirrorurls
        # if our mirrorlist is just screwed then make sure we unlink a mirrorlist cache
        if len(self._urls) < 1:
            if hasattr(self, 'mirrorlist_file') and os.path.exists(self.mirrorlist_file):
                if not self.cache:
                    try:
                        misc.unlink_f(self.mirrorlist_file)
                    except (IOError, OSError), e:
                        print 'Could not delete bad mirrorlist file: %s - %s' % (self.mirrorlist_file, e)
                    else:
                        print 'removing mirrorlist with no valid mirrors: %s' % self.mirrorlist_file
        # store them all back in baseurl for compat purposes
        self.baseurl = self._urls
        self.check()

    def _replace_and_check_url(self, url_list):
        goodurls = []
        skipped = None
        for url in url_list:
            # obvious bogons get ignored b/c, we could get more interesting checks but <shrug>
            if url in ['', None]:
                continue
            url = parser.varReplace(url, self.yumvar)
            if url[-1] != '/':
                url= url + '/'
            try:
                # This started throwing ValueErrors, BZ 666826
                (s,b,p,q,f,o) = urlparse.urlparse(url)
            except (ValueError, IndexError, KeyError), e:
                s = 'blah'

            if s not in ['http', 'ftp', 'file', 'https']:
                skipped = url
                continue
            else:
                goodurls.append(url)

        if skipped is not None:
            # Caller cleans up for us.
            if goodurls:
                print 'YumRepo Warning: Some mirror URLs are not using ftp, http[s] or file.\n Eg. %s' % misc.to_utf8(skipped)
            else: # And raises in this case
                print 'YumRepo Error: All mirror URLs are not using ftp, http[s] or file.\n Eg. %s' % misc.to_utf8(skipped)
        return goodurls

    def _geturls(self):
        if not self._urls:
            self._baseurlSetup()
        return self._urls

    urls = property(fget=lambda self: self._geturls(),
                    fset=lambda self, value: setattr(self, "_urls", value),
                    fdel=lambda self: setattr(self, "_urls", None))

    def _getMetalink(self):
        if not self._metalink:
            self.metalink_filename = self.cachedir + '/' + 'metalink.xml'
            local = self.metalink_filename + '.tmp'
            if not self._metalinkCurrent():
                url = misc.to_utf8(self.metalink)
                ugopts = self._default_grabopts(cache=self.http_caching=='all')
                try:
                    ug = URLGrabber(progress_obj = self.callback, **ugopts)
                    result = ug.urlgrab(url, local, text="%s/metalink" % self.ui_id)

                except URLGrabError, e:
                    if not os.path.exists(self.metalink_filename):
                        msg = ("Cannot retrieve metalink for repository: %s. "
                               "Please verify its path and try again" % self.ui_id )
                        raise Errors.RepoError, msg
                    #  Now, we have an old usable metalink, so we can't move to
                    # a newer repomd.xml ... or checksums won't match.
                    print "Could not get metalink %s error was\n%s: %s" % (url, e.args[0], misc.to_unicode(e.args[1]))                    
                    self._metadataCurrent = True

            if not self._metadataCurrent:
                try:
                    self._metalink = metalink.MetaLinkRepoMD(result)
                    shutil.move(result, self.metalink_filename)
                except metalink.MetaLinkRepoErrorParseFail, e:
                    # Downloaded file failed to parse, revert (dito. above):
                    print "Could not parse metalink %s error was \n%s"%(url, e)
                    self._metadataCurrent = True
                    misc.unlink_f(result)

            if self._metadataCurrent:
                self._metalink = metalink.MetaLinkRepoMD(self.metalink_filename)

        return self._metalink

    metalink_data = property(fget=lambda self: self._getMetalink(),
                             fset=lambda self, value: setattr(self, "_metalink",
                                                              value),
                             fdel=lambda self: setattr(self, "_metalink", None))

    def _all_urls_are_files(self, url):
        if url:
            return url.startswith("/") or url.startswith("file:")

        if not self.urls: # WTF ... but whatever.
            return False

        # Not an explicit url ... so make sure all mirrors/etc. are file://
        for url in self.urls:
            if not self._all_urls_are_files(url):
                return False
        return True

    def _getFile(self, url=None, relative=None, local=None, start=None, end=None,
            copy_local=None, checkfunc=None, text=None, reget='simple', 
            cache=True, size=None, **kwargs):
        """retrieve file from the mirrorgroup for the repo
           relative to local, optionally get range from
           start to end, also optionally retrieve from a specific baseurl"""

        # if local or relative is None: raise an exception b/c that shouldn't happen
        # if url is not None - then do a grab from the complete url - not through
        # the mirror, raise errors as need be
        # if url is None do a grab via the mirror group/grab for the repo
        # return the path to the local file

        # if copylocal isn't specified pickup the repo-defined attr
        if copy_local is None:
            copy_local = self.copy_local

        if local is None or relative is None:
            raise Errors.RepoError, \
                  "get request for Repo %s, gave no source or dest" % self.ui_id

        if self.cache == 1:
            if os.path.exists(local): # FIXME - we should figure out a way
                return local          # to run the checkfunc from here

            else: # ain't there - raise
                raise Errors.RepoError, \
                    "Caching enabled but no local cache of %s from %s" % (local,

                           self.ui_id)

        if url:
            (scheme, netloc, path, query, fragid) = urlparse.urlsplit(url)

        if self.mediaid and self.mediafunc:
            discnum = 1
            if url:
                if scheme == "media" and fragid:
                    discnum = int(fragid)
            try:
                # FIXME: we need to figure out what really matters to
                # pass to the media grabber function here
                result = self.mediafunc(local = local, checkfunc = checkfunc, relative = relative, text = text, copy_local = copy_local, url = url, mediaid = self.mediaid, name = self.name, discnum = discnum, range = (start, end))
                return result
            except Errors.MediaError, e:
                verbose_logger.log(logginglevels.DEBUG_2, "Error getting package from media; falling back to url %s" %(e,))

        if size and (copy_local or not self._all_urls_are_files(url)):
            dirstat = os.statvfs(os.path.dirname(local))
            avail = dirstat.f_bavail * dirstat.f_bsize
            if avail < long(size):
                raise Errors.RepoError, _('''\
Insufficient space in download directory %s
    * free   %s
    * needed %s'''
                ) % (os.path.dirname(local), format_number(avail), format_number(long(size)))

        if url and scheme != "media":
            ugopts = self._default_grabopts(cache=cache)
            ug = URLGrabber(progress_obj = self.callback,
                            copy_local = copy_local,
                            reget = reget,
                            failure_callback = self.failure_obj,
                            interrupt_callback=self.interrupt_callback,
                            checkfunc=checkfunc,
                            size=size,
                            **ugopts)

            remote = url + '/' + relative

            try:
                result = ug.urlgrab(misc.to_utf8(remote), local,
                                    text=misc.to_utf8(text),
                                    range=(start, end),
                                    )
            except URLGrabError, e:
                self._del_dl_file(local, size)
                errstr = "failed to retrieve %s from %s\nerror was %s" % (relative, self, e)
                e = Errors.RepoError(errstr)
                e.repo = self
                raise e

        else:
            headers = tuple(self.__headersListFromDict(cache=cache))
            try:
                result = self.grab.urlgrab(misc.to_utf8(relative), local,
                                           text = misc.to_utf8(text),
                                           range = (start, end),
                                           copy_local=copy_local,
                                           reget = reget,
                                           checkfunc=checkfunc,
                                           http_headers=headers,
                                           size=size,
                                           **kwargs
                                           )
            except URLGrabError, e:
                self._del_dl_file(local, size)
                errstr = "failure: %s from %s: %s" % (relative, self, e)
                errors = getattr(e, 'errors', None)
                e = Errors.NoMoreMirrorsRepoError(errstr, errors)
                e.repo = self
                raise e

        return result
    __get = _getFile

    def getPackage(self, package, checkfunc=None, text=None, cache=True, **kwargs):
        remote = package.relativepath
        local = package.localPkg()
        basepath = package.basepath

        if self._preload_pkg_from_system_cache(package):
            if package.verifyLocalPkg():
                return local
            misc.unlink_f(local)

        if checkfunc is None:
            def checkfunc(obj):
                if not package.verifyLocalPkg():
                    misc.unlink_f(local)
                    raise URLGrabError(-1, _('Package does not match intended download.'))

        ret = self._getFile(url=basepath,
                        relative=remote,
                        local=local,
                        checkfunc=checkfunc,
                        text=text,
                        cache=cache,
                        size=package.size,
                        **kwargs
                        )

        if not kwargs.get('async') and not package.verifyLocalPkg():
            # Don't return as "success" when bad.
            msg = "Downloaded package %s, from %s, but it was invalid."
            msg = msg % (package, package.repo.id)
            raise Errors.RepoError, msg

        return ret

    def getHeader(self, package, checkfunc = None, reget = 'simple',
            cache = True):

        remote = package.relativepath
        local =  package.localHdr()
        start = package.hdrstart
        end = package.hdrend
        size = end-start
        basepath = package.basepath
        # yes, I know, don't ask
        if not os.path.exists(self.hdrdir):
            os.makedirs(self.hdrdir)

        return self._getFile(url=basepath, relative=remote, local=local, start=start,
                        reget=None, end=end, checkfunc=checkfunc, copy_local=1,
                        cache=cache, size=size,
                        )

    def metadataCurrent(self):
        """Check if there is a metadata_cookie and check its age. If the
        age of the cookie is less than metadata_expire time then return true
        else return False. This result is cached, so that metalink/repomd.xml
        are synchronized."""
        if self._metadataCurrent is not None:
            return self._metadataCurrent

        mC_def = self.withinCacheAge(self.metadata_cookie, self.metadata_expire)
        if not mC_def: # Normal path...
            return mC_def

        # Edge cases, both repomd.xml and metalink (if used). Must exist.
        repomdfn = self.cachedir + '/' + 'repomd.xml'
        if not os.path.exists(repomdfn):
            return False

        self._hack_mirrorlist_for_anaconda()
        mlfn = self.cachedir + '/' + 'metalink.xml'
        if self.metalink and not os.path.exists(mlfn):
            return False

        self._metadataCurrent = True
        return True

    #  The metalink _shouldn't_ be newer than the repomd.xml or the checksums
    # will be off, but we only really care when we are downloading the
    # repomd.xml ... so keep it in mind that they can be off on disk.
    #  Also see _getMetalink()
    def _metalinkCurrent(self):
        if self._metadataCurrent is not None:
            return self._metadataCurrent

        if self.cache and not os.path.exists(self.metalink_filename):
            raise Errors.RepoError, 'Cannot find metalink.xml file for %s' %self

        if self.cache:
            self._metadataCurrent = True
        elif not os.path.exists(self.metalink_filename):
            self._metadataCurrent = False
        elif self.withinCacheAge(self.metadata_cookie, self.metadata_expire):
            self._metadataCurrent = True
        else:
            self._metadataCurrent = False
        return self._metadataCurrent

    def withinCacheAge(self, myfile, expiration_time, expire_req_filter=True):
        """check if any file is older than a certain amount of time. Used for
           the cachecookie and the mirrorlist
           return True if w/i the expiration time limit
           false if the time limit has expired

           Additionally compare the file to age of the newest .repo or yum.conf
           file. If any of them are newer then invalidate the cache
           """

        # Never/write means we just skip this...
        if (expire_req_filter and hasattr(self, '_metadata_cache_req') and
            self._metadata_cache_req.startswith("read-only:") and
            self.metadata_expire_filter.startswith("read-only:")):

            cache_filt = self.metadata_expire_filter[len("read-only:"):]
            cache_req  = self._metadata_cache_req[len("read-only:"):]

            if cache_filt == 'future':
                assert cache_req in ('past', 'present', 'future')
                expiration_time = -1
            if cache_filt == 'present':
                if cache_req in ('past', 'present'):
                    expiration_time = -1
            if cache_filt == 'past':
                if cache_req == 'past':
                    expiration_time = -1

        # -1 is special and should never get refreshed
        if expiration_time == -1 and os.path.exists(myfile):
            return True
        val = False
        if os.path.exists(myfile):
            cookie_info = os.stat(myfile)
            if cookie_info[8] + expiration_time > time.time():
                val = True
            # WE ARE FROM THE FUTURE!!!!
            elif cookie_info[8] > time.time():
                val = False

            if not self.check_config_file_age:
                return val

            # make sure none of our config files for this repo are newer than
            # us
            if cookie_info[8] < int(self.repo_config_age):
                val = False

        return val

    def setMetadataCookie(self):
        """if possible, set touch the metadata_cookie file"""

        check = self.metadata_cookie
        if not os.path.exists(self.metadata_cookie):
            check = self.cachedir

        if os.access(check, os.W_OK):
            fo = open(self.metadata_cookie, 'w+')
            fo.close()
            del fo

    def setup(self, cache, mediafunc = None, gpg_import_func=None, confirm_func=None, gpgca_import_func=None):
        try:
            self.cache = cache
            self.mediafunc = mediafunc
            self.gpg_import_func = gpg_import_func
            self.gpgca_import_func = gpgca_import_func
            self.confirm_func = confirm_func
        except Errors.RepoError, e:
            raise
        if not self.mediafunc and self.mediaid and not self.mirrorlist and not self.baseurl:
            verbose_logger.log(logginglevels.DEBUG_2, "Disabling media repo for non-media-aware frontend")
            self.enabled = False
            self.skip_if_unavailable = True

    def _cachingRepoXML(self, local):
        """ Should we cache the current repomd.xml """
        if self.cache and not os.path.exists(local):
            raise Errors.RepoError, 'Cannot find repomd.xml file for %s' % self.ui_id
        if self.cache or self.metadataCurrent():
            return True
        return False

    def _getFileRepoXML(self, local, text=None, grab_can_fail=None):
        """ Call _getFile() for the repomd.xml file. """
        checkfunc = (self._checkRepoXML, (), {})
        if grab_can_fail is None:
            grab_can_fail = 'old_repo_XML' in self._oldRepoMDData
        tfname = ''
        try:
            # This is named so that "yum clean metadata" picks it up
            tfname = tempfile.mktemp(prefix='repomd', suffix="tmp.xml",
                                     dir=os.path.dirname(local))
            result = self._getFile(relative=self.repoMDFile,
                                   local=tfname,
                                   copy_local=1,
                                   text=text,
                                   reget=None,
                                   checkfunc=checkfunc,
                                   cache=self.http_caching == 'all',
                                   size=102400) # setting max size as 100K

        except URLGrabError, e:
            misc.unlink_f(tfname)
            if grab_can_fail:
                return None
            raise Errors.RepoError, 'Error downloading file %s: %s' % (local, e)
        except Errors.RepoError:
            misc.unlink_f(tfname)
            if grab_can_fail:
                return None
            raise

        # This should always work...
        try:
            os.rename(result, local)
        except:
            # But in case it doesn't...
            misc.unlink_f(tfname)
            if grab_can_fail:
                return None
            raise Errors.RepoError, 'Error renaming file %s to %s' % (result,
                                                                      local)
        return local

    def _parseRepoXML(self, local, parse_can_fail=None):
        """ Parse the repomd.xml file. """
        try:
            return repoMDObject.RepoMD(self.id, local)
        except Errors.RepoMDError, e:
            if parse_can_fail is None:
                parse_can_fail = 'old_repo_XML' in self._oldRepoMDData
            if parse_can_fail:
                return None
            raise Errors.RepoError, 'Error importing repomd.xml from %s: %s' % (self.ui_id, e)

    def _saveOldRepoXML(self, local):
        """ If we have an older repomd.xml file available, save it out. """
        # Cleanup old trash...
        for fname in glob.glob(self.cachedir + "/*.old.tmp"):
            misc.unlink_f(fname)

        if os.path.exists(local):
            old_local = local + '.old.tmp' # locked, so this is ok
            shutil.copy2(local, old_local)
            xml = self._parseRepoXML(old_local, True)
            if xml is None:
                return None
            self._oldRepoMDData = {'old_repo_XML' : xml, 'local' : local,
                                   'old_local' : old_local, 'new_MD_files' : []}
            return xml
        return None

    def _revertOldRepoXML(self):
        """ If we have older data available, revert to it. """

        #  If we can't do a timestamp check, then we can be looking at a
        # completely different repo. from last time ... ergo. we can't revert.
        #  We still want the old data, so we don't download twice. So we
        # pretend everything is good until the revert.
        if not self.timestamp_check:
            raise Errors.RepoError, "Can't download or revert repomd.xml for %s" % self.ui_id

        if 'old_repo_XML' not in self._oldRepoMDData:
            self._oldRepoMDData = {}
            return

        # Unique names mean the rename doesn't work anymore.
        for fname in self._oldRepoMDData['new_MD_files']:
            misc.unlink_f(fname)

        old_data = self._oldRepoMDData
        self._oldRepoMDData = {}

        if 'old_local' in old_data:
            os.rename(old_data['old_local'], old_data['local'])

        self._repoXML = old_data['old_repo_XML']

        if 'old_MD_files' not in old_data:
            return
        for revert in old_data['old_MD_files']:
            os.rename(revert + '.old.tmp', revert)

    def _doneOldRepoXML(self):
        """ Done with old data, delete it. """
        old_data = self._oldRepoMDData
        self._oldRepoMDData = {}

        if 'old_local' in old_data:
            misc.unlink_f(old_data['old_local'])

        if 'old_MD_files' not in old_data:
            return
        for revert in old_data['old_MD_files']:
            misc.unlink_f(revert + '.old.tmp')

    def _get_mdtype_data(self, mdtype, repoXML=None):
        if repoXML is None:
            repoXML = self.repoXML

        if mdtype == 'group' and 'group_gz' in repoXML.fileTypes():
            mdtype = 'group_gz'
        if (mdtype in ['other', 'filelists', 'primary'] and
            self._check_db_version(mdtype + '_db', repoXML=repoXML)):
            mdtype += '_db'

        return (mdtype, repoXML.repoData.get(mdtype))

    def _get_mdtype_fname(self, data, compressed=False):
        (r_base, remote) = data.location
        local = self.cachedir + '/' + os.path.basename(remote)

        if compressed: # DB file, we need the uncompressed version
            local = misc.decompress(local, fn_only=True)
        return local

    def _groupCheckDataMDNewer(self):
        """ We check the timestamps, if any of the timestamps for the
            "new" data is older than what we have ... we revert. """

        if 'old_repo_XML' not in self._oldRepoMDData:
            return True
        old_repo_XML = self._oldRepoMDData['old_repo_XML']

        if (self.timestamp_check and
            old_repo_XML.timestamp > self.repoXML.timestamp):
            logger.warning("Not using downloaded repomd.xml because it is "
                           "older than what we have:\n"
                           "  Current   : %s\n  Downloaded: %s" %
                           (time.ctime(old_repo_XML.timestamp),
                            time.ctime(self.repoXML.timestamp)))
            return False
        return True

    @staticmethod
    def _checkRepoXMLMetalink(repoXML, repomd):
        """ Check parsed repomd.xml against metalink.repomd data. """
        if repoXML.timestamp != repomd.timestamp:
            return False
        if repoXML.length != repomd.size:
            return False

        done = False
        for checksum in repoXML.checksums:
            if checksum not in repomd.chksums:
                continue

            if repoXML.checksums[checksum] != repomd.chksums[checksum]:
                return False

            #  All checksums should be trusted, but if we have more than one
            # then we might as well check them all ... paranoia is good.
            done = True

        return done

    def _checkRepoMetalink(self, repoXML=None, metalink_data=None):
        """ Check the repomd.xml against the metalink data, if we have it. """

        if repoXML is None:
            repoXML = self._repoXML
        if metalink_data is None:
            metalink_data = self.metalink_data

        if self._checkRepoXMLMetalink(repoXML, metalink_data.repomd):
            return True

        # FIXME: We probably want to skip to the first mirror which has the
        # latest repomd.xml, but say "if we can't find one, use the newest old
        # repomd.xml" ... alas. that's not so easy to do in urlgrabber atm.
        for repomd in self.metalink_data.old_repomds:
            if self._checkRepoXMLMetalink(repoXML, repomd):
                verbose_logger.log(logginglevels.DEBUG_2,
                                   "Using older repomd.xml\n"
                                   "  Latest: %s\n"
                                   "  Using: %s" %
                                   (time.ctime(metalink_data.repomd.timestamp),
                                    time.ctime(repomd.timestamp)))
                return True
        return False

    def _latestRepoXML(self, local):
        """ Save the Old Repo XML, and if it exists check to see if it's the
            latest available given the metalink data. """

        oxml = self._saveOldRepoXML(local)
        if not oxml: # No old repomd.xml data
            return False

        self._hack_mirrorlist_for_anaconda()
        if not self.metalink: # Nothing to check it against
            return False

        # Get the latest metalink, and the latest repomd data from it
        repomd = self.metalink_data.repomd

        if self.timestamp_check and oxml.timestamp > repomd.timestamp:
            #  We have something "newer" than the latest, and have timestamp
            # checking which will kill anything passing the metalink check.
            return True

        # Do we have the latest repomd already
        return self._checkRepoXMLMetalink(oxml, repomd)

    def _commonLoadRepoXML(self, text, mdtypes=None):
        """ Common LoadRepoXML for instant and group, returns False if you
            should just return. """
        local  = self.cachedir + '/repomd.xml'
        if self._repoXML is not None:
            return False

        if self._cachingRepoXML(local):
            caching = True
            result = local
        else:
            caching = False
            if self._latestRepoXML(local):
                result = local
                old_data = self._oldRepoMDData
                self._repoXML = old_data['old_repo_XML']
            else:
                result = self._getFileRepoXML(local, text)
                if result is None:
                    # Ignore this as we have a copy
                    self._revertOldRepoXML()
                    return False

            # if we have a 'fresh' repomd.xml then update the cookie
            self.setMetadataCookie()

        if self._repoXML is None:
            self._repoXML = self._parseRepoXML(result)
        if self._repoXML is None:
            self._revertOldRepoXML()
            return False

        if caching:
            return False # Skip any work.

        if not self._groupCheckDataMDNewer():
            self._revertOldRepoXML()
            return False
        return True

    def _check_db_version(self, mdtype, repoXML=None):
        if self.mddownloadpolicy == 'xml':
            return False

        if repoXML is None:
            repoXML = self.repoXML
        if mdtype in repoXML.repoData:
            if DBVERSION == repoXML.repoData[mdtype].dbversion:
                return True
        return False

    # mmdtype is unused, but in theory was == primary
    # dbmtype == primary_db etc.
    def _groupCheckDataMDValid(self, data, dbmdtype, mmdtype, file_check=False):
        """ Check that we already have this data, and that it's valid. Given
            the DB mdtype and the main mdtype (no _db suffix). """

        if data is None:
            return None

        if not file_check:
            compressed = False
            local = self._get_mdtype_fname(data)
        else:
            compressed = False
            local = self._get_mdtype_fname(data)
            if not os.path.exists(local):
                local = misc.decompress(local, fn_only=True)
                compressed = True
        #  If we can, make a copy of the system-wide-cache version of this file,
        # note that we often don't get here. So we also do this in
        # YumPackageSack.populate ... and we look for the uncompressed versions
        # in retrieveMD.
        self._preload_md_from_system_cache(os.path.basename(local))
        if not self._checkMD(local, dbmdtype, openchecksum=compressed,
                             data=data, check_can_fail=True):
            return None

        return local

    def _commonRetrieveDataMD(self, mdtypes=None):
        """ Retrieve any listed mdtypes, and revert if there was a failure.
            Also put any of the non-valid mdtype files from the old_repo_XML
            into the delete list, this means metadata can change filename
            without us leaking it. """

        downloading = self._commonRetrieveDataMD_list(mdtypes)
        for (ndata, nmdtype) in downloading:
            if not self._retrieveMD(nmdtype, retrieve_can_fail=True):
                self._revertOldRepoXML()
                return False
        self._commonRetrieveDataMD_done(downloading)
        return True

    def _commonRetrieveDataMD_list(self, mdtypes):
        """ Return a list of metadata to be retrieved """

        def _mdtype_eq(omdtype, odata, nmdtype, ndata):
            """ Check if two returns from _get_mdtype_data() are equal. """
            if ndata is None:
                return False
            if omdtype != nmdtype:
                return False
            if odata.checksum != ndata.checksum:
                return False
            #  If we turn --unique-md-filenames on without chaning the data,
            # then we'll get different filenames, but the same checksum.
            #  Atm. just say they are different, to make sure we delete the
            # old files.
            orname = os.path.basename(odata.location[1])
            nrname = os.path.basename(ndata.location[1])
            if orname != nrname:
                return False
            return True

        all_mdtypes = self.retrieved.keys()
        # Add in any extra stuff we don't know about.
        for mdtype in self.repoXML.fileTypes():
            if mdtype in all_mdtypes:
                continue
            if mdtype in ('primary_db', 'filelists_db', 'other_db', 'group_gz'):
                continue
            all_mdtypes.append(mdtype)

        if mdtypes is None:
            mdtypes = all_mdtypes

        reverts = []
        if 'old_repo_XML' not in self._oldRepoMDData:
            old_repo_XML = None
        else:
            old_repo_XML = self._oldRepoMDData['old_repo_XML']
            self._oldRepoMDData['old_MD_files'] = reverts

        # Inited twice atm. ... sue me
        newmdfiles = self._oldRepoMDData['new_MD_files'] = []
        downloading = []
        for mdtype in all_mdtypes:
            (nmdtype, ndata) = self._get_mdtype_data(mdtype)

            if old_repo_XML:
                (omdtype, odata) = self._get_mdtype_data(mdtype,
                                                         repoXML=old_repo_XML)
                local = self._groupCheckDataMDValid(odata, omdtype,mdtype,True)
                if local:
                    if _mdtype_eq(omdtype, odata, nmdtype, ndata):
                        continue # If they are the same do nothing

                    # Move this version, we _may_ get a new one.
                    # We delete it on success, revert it back on failure.
                    # We don't copy as we know it's bad due to above test.
                    os.rename(local, local + '.old.tmp')
                    reverts.append(local)

                    #  This is the super easy way. We just to see if a generated
                    # file is there for all files, but it should always work.
                    #  And anyone who is giving us MD with blah and blah.sqlite
                    # which are different types, can play a game I like to call
                    # "come here, ouch".
                    gen_local = local + '.sqlite'
                    if os.path.exists(gen_local):
                        os.rename(gen_local, gen_local + '.old.tmp')
                        reverts.append(gen_local)

            if ndata is None: # Doesn't exist in this repo
                continue

            if mdtype not in mdtypes:
                continue

            # No old repomd data, but we might still have uncompressed MD
            if self._groupCheckDataMDValid(ndata, nmdtype, mdtype):
                continue
            downloading.append((ndata, nmdtype))
            newmdfiles.append(self._get_mdtype_fname(ndata, False))
        return downloading

    def _commonRetrieveDataMD_done(self, downloading):
        """ Uncompress the downloaded metadata """

        for (ndata, nmdtype) in downloading:
            local = self._get_mdtype_fname(ndata, False)
        self._doneOldRepoXML()

    def _groupLoadRepoXML(self, text=None, mdtypes=None):
        """ Retrieve the new repomd.xml from the repository, then check it
            and parse it. If it fails we revert to the old version and pretend
            that is fine. If the new repomd.xml requires new version of files
            that we have, like updateinfo.xml, we download those too and if any
            of those fail, we again revert everything and pretend old data is
            good. """

        if self._commonLoadRepoXML(text):
            self._commonRetrieveDataMD(mdtypes)

    def _mdpolicy2mdtypes(self):
        md_groups = {'instant'       : ['__None__'],
                     'group:primary' : ['primary'],
                     'group:small'   : ["primary", "updateinfo", "group", "pkgtags"],
                     'group:main'    : ["primary", "updateinfo", "group", "pkgtags",
                                        "filelists", "prestodelta"]}
        mdtypes = set()
        if type(self.mdpolicy) in types.StringTypes:
            mdtypes.update(md_groups.get(self.mdpolicy, [self.mdpolicy]))
        else:
            for mdpolicy in self.mdpolicy:
                mdtypes.update(md_groups.get(mdpolicy, [mdpolicy]))

        if not mdtypes or 'group:all' in mdtypes:
            mdtypes = None
        else:
            mdtypes.discard("__None__")
            mdtypes = sorted(list(mdtypes))
        return mdtypes

    def _loadRepoXML(self, text=None):
        """retrieve/check/read in repomd.xml from the repository"""
        try:
            return self._groupLoadRepoXML(text, self._mdpolicy2mdtypes())
        except KeyboardInterrupt:
            self._revertOldRepoXML() # Undo metadata cookie?
            raise
        raise Errors.RepoError, 'Bad loadRepoXML policy (for %s): %s' % (self.ui_id, self.mdpolicy)

    def _getRepoXML(self):
        if self._repoXML:
            return self._repoXML
        self._loadRepoXML(text=self.ui_id)
        return self._repoXML


    repoXML = property(fget=lambda self: self._getRepoXML(),
                       fset=lambda self, val: setattr(self, "_repoXML", val),
                       fdel=lambda self: setattr(self, "_repoXML", None))

    def _checkRepoXML(self, fo):
        if type(fo) is types.InstanceType:
            filepath = fo.filename
        else:
            filepath = fo

        if self.repo_gpgcheck and not self._override_sigchecks:

            if misc.gpgme is None:
                raise URLGrabError(-1, 'pygpgme is not working so repomd.xml can not be verified for %s' % (self))

            sigfile = self.cachedir + '/repomd.xml.asc'
            try:
                result = self._getFile(relative='repodata/repomd.xml.asc',
                                       copy_local=1,
                                       local = sigfile,
                                       text='%s/signature' % self.ui_id,
                                       reget=None,
                                       checkfunc=None,
                                       cache=self.http_caching == 'all',
                                       size=102400)
            except URLGrabError, e:
                raise URLGrabError(-1, 'Error finding signature for repomd.xml for %s: %s' % (self, e))
            valid = misc.valid_detached_sig(result, filepath, self.gpgdir)
            if not valid and self.gpg_import_func:
                try:
                    self.gpg_import_func(self, self.confirm_func)
                except Errors.YumBaseError, e:
                    raise URLGrabError(-1, 'Gpg Keys not imported, cannot verify repomd.xml for repo %s' % (self))
                valid = misc.valid_detached_sig(result, filepath, self.gpgdir)

            if not valid:
                raise URLGrabError(-1, 'repomd.xml signature could not be verified for %s' % (self))

        try:
            repoXML = repoMDObject.RepoMD(self.id, filepath)
        except Errors.RepoMDError, e:
            raise URLGrabError(-1, 'Error importing repomd.xml for %s: %s' % (self, e))

        self._hack_mirrorlist_for_anaconda()
        if self.metalink and not self._checkRepoMetalink(repoXML):
            raise URLGrabError(-1, 'repomd.xml does not match metalink for %s' %
                               self)

    def _del_dl_file(self, local, size):
        """ Delete a downloaded file if it's the correct size. """

        sd = misc.stat_f(local)
        if not sd: # File doesn't exist...
            return

        if size and sd.st_size < size:
            return # Still more to get...

        # Is the correct size, or too big ... delete it so we'll try again.
        misc.unlink_f(local)

    def checkMD(self, fn, mdtype, openchecksum=False):
        """check the metadata type against its checksum"""
        return self._checkMD(fn, mdtype, openchecksum)

    def _checkMD(self, fn, mdtype, openchecksum=False,
                 data=None, check_can_fail=False):
        """ Internal function, use .checkMD() from outside yum. """

        thisdata = data # So the argument name is nicer
        if thisdata is None:
            thisdata = self.repoXML.getData(mdtype)

        # Note openchecksum means do it after you've uncompressed the data.
        if openchecksum:
            (r_ctype, r_csum) = thisdata.openchecksum # get the remote checksum
            size = thisdata.opensize
        else:
            (r_ctype, r_csum) = thisdata.checksum # get the remote checksum
            size = thisdata.size

        if type(fn) == types.InstanceType: # this is an urlgrabber check
            file = fn.filename
        else:
            file = fn

        if size is not None:
            size = int(size)

        l_csum = _xattr_get_chksum(file, r_ctype)
        if l_csum:
            fsize = misc.stat_f(file)
            if fsize is not None: # We just got an xattr, so it should be there
                if size is None and l_csum == r_csum and fsize.st_size > 0:
                    return 1
                if size == fsize.st_size and l_csum == r_csum:
                    return 1
            # Anything goes wrong, run the checksums as normal...

        try: # get the local checksum
            l_csum = self._checksum(r_ctype, file, datasize=size)
        except Errors.RepoError, e:
            if check_can_fail:
                return None
            raise URLGrabError(-3, 'Error performing checksum')

        if l_csum == r_csum:
            _xattr_set_chksum(file, r_ctype, l_csum)
            return 1
        else:
            if check_can_fail:
                return None
            raise URLGrabError(-1, 'Metadata file does not match checksum')

    def retrieveMD(self, mdtype):
        """base function to retrieve metadata files from the remote url
           returns the path to the local metadata file of a 'mdtype'
           mdtype can be 'primary', 'filelists', 'other' or 'group'."""
        return self._retrieveMD(mdtype)

    def _retrieveMD(self, mdtype, retrieve_can_fail=False, **kwargs):
        """ Internal function, use .retrieveMD() from outside yum. """
        #  Note that this can raise Errors.RepoMDError if mdtype doesn't exist
        # for this repo.
        # FIXME - maybe retrieveMD should call decompress() after we've checked
        # the checksum by default? since we're never acting on compressed MD
        thisdata = self.repoXML.getData(mdtype)

        (r_base, remote) = thisdata.location
        fname = os.path.basename(remote)
        local = self.cachedir + '/' + fname

        if self.retrieved.get(mdtype):
            # got it, move along
            return local

        if (os.path.exists(local) or
            self._preload_md_from_system_cache(os.path.basename(local))):
            if self._checkMD(local, mdtype, check_can_fail=True):
                self.retrieved[mdtype] = 1
                return local # it's the same return the local one

        if self.cache == 1:
            if retrieve_can_fail:
                return None
            if os.path.exists(local):
                msg = "Caching enabled and local cache: %s does not match checksum" % local
            else:
                msg = "Caching enabled but no local cache of %s from %s" % (local, self.ui_id)
            raise Errors.RepoError, msg

        try:
            def checkfunc(obj):
                try:
                    self.checkMD(obj, mdtype)
                except URLGrabError:
                    #  Don't share MD among mirrors, in theory we could use:
                    #     self._del_dl_file(local, int(thisdata.size))
                    # ...but this is safer.
                    misc.unlink_f(obj.filename)
                    raise
                self.retrieved[mdtype] = 1
            text = "%s/%s" % (self.ui_id, mdtype)
            if thisdata.size is None:
                reget = None
            else:
                reget = 'simple'
                self._del_dl_file(local, int(thisdata.size))
            local = self._getFile(relative=remote,
                                  local=local, 
                                  copy_local=1,
                                  reget=reget,
                                  checkfunc=checkfunc, 
                                  text=text,
                                  cache=self.http_caching == 'all',
                                  size=thisdata.size,
                                  **kwargs)
        except Errors.RepoError:
            if retrieve_can_fail:
                return None
            raise
        except URLGrabError, e:
            if retrieve_can_fail:
                return None
            raise Errors.RepoError, \
                "Could not retrieve %s matching remote checksum from %s" % (local, self.ui_id)
        else:
            return local


    def getPrimaryXML(self):
        """this gets you the path to the primary.xml file, retrieving it if we
           need a new one"""

        return self.retrieveMD('primary')


    def getFileListsXML(self):
        """this gets you the path to the filelists.xml file, retrieving it if we
           need a new one"""

        return self.retrieveMD('filelists')

    def getOtherXML(self):
        return self.retrieveMD('other')

    def getGroups(self):
        """gets groups and returns group file path for the repository, if there
           is none or retrieve/decompress fails, it returns None"""
        if 'group_gz' in self.repoXML.fileTypes():
            fn = self._retrieveMD('group_gz', retrieve_can_fail=True)
            if fn:
                try:
                    fn = misc.repo_gen_decompress(fn, 'comps.xml', cached=self.cache)
                except IOError, e:
                    logger.warning(e)
                    fn = None
            return fn
        return self._retrieveMD('group', retrieve_can_fail=True)

    def setCallback(self, callback, multi_callback=None):
        self.callback = callback
        self.multi_callback = multi_callback
        self._callbacks_changed = True

    def setFailureObj(self, failure_obj):
        self.failure_obj = failure_obj
        self._callbacks_changed = True

    def setMirrorFailureObj(self, failure_obj):
        self.mirror_failure_obj = failure_obj
        self._callbacks_changed = True

    def setInterruptCallback(self, callback):
        self.interrupt_callback = callback
        self._callbacks_changed = True

    def _readMirrorList(self, fo, url=None):
        """ read the mirror list from the specified file object """
        returnlist = []

        content = []
        if fo is not None:
            try:
                content = fo.readlines()
            except Exception, e:
                if url is None: # Shouldn't happen
                    url = "<unknown>"
                print "Could not read mirrorlist %s, error was \n%s" %(url, e)
                content = []
            for line in content:
                if not re.match('\w+://\S+\s*$', line):
                    continue
                mirror = line.rstrip() # no more trailing \n's
                mirror = mirror.replace('$ARCH', '$BASEARCH')
                returnlist.append(mirror)

        return (returnlist, content)

    def _getMirrorList(self):
        """retrieve an up2date-style mirrorlist file from our mirrorlist url,
           also save the file to the local repo dir and use that if cache expiry
           not expired

           we also s/$ARCH/$BASEARCH/ and move along
           return the baseurls from the mirrorlist file
           """
        self.mirrorlist_file = self.cachedir + '/' + 'mirrorlist.txt'
        fo = None

        cacheok = False
        if self.withinCacheAge(self.mirrorlist_file, self.mirrorlist_expire,
                               expire_req_filter=False):
            cacheok = True
            fo = open(self.mirrorlist_file, 'r')
            url = 'file://' + self.mirrorlist_file # just to keep self._readMirrorList(fo,url) happy
        else:
            url = self.mirrorlist
            scheme = urlparse.urlparse(url)[0]
            if scheme == '':
                url = 'file://' + url
            ugopts = self._default_grabopts()
            try:
                fo = urlgrabber.grabber.urlopen(url, **ugopts)
            except URLGrabError, e:
                print "Could not retrieve mirrorlist %s error was\n%s: %s" % (url, e.args[0], misc.to_unicode(e.args[1]))
                fo = None

        (returnlist, content) = self._readMirrorList(fo, url)

        if returnlist:
            if not self.cache and not cacheok:
                output = open(self.mirrorlist_file, 'w')
                for line in content:
                    output.write(line)
                output.close()
        elif not cacheok and os.path.exists(self.mirrorlist_file):
            # New mirror file failed, so use the old one (better than nothing)
            os.utime(self.mirrorlist_file, None)
            return self._readMirrorList(open(self.mirrorlist_file, 'r'))[0]

        return returnlist

    def _preload_file(self, fn, destfn):
        """attempts to copy the file, if possible"""
        # don't copy it if the copy in our users dir is newer or equal
        if not os.path.exists(fn):
            return False
        if os.path.exists(destfn):
            if os.stat(fn)[stat.ST_CTIME] <= os.stat(destfn)[stat.ST_CTIME]:
                return False
        try:
            # IOError is the main culprit, with mode=600. But ignore everything.
            shutil.copy2(fn, destfn)
        except:
            return False
        return True

    def _preload_file_from_system_cache(self, filename, subdir='',
                                        destfn=None):
        """attempts to copy the file from the system-wide cache,
           if possible"""
        if not hasattr(self, 'old_base_cache_dir'):
            return False
        if self.old_base_cache_dir == "":
            return False

        glob_repo_cache_dir=os.path.join(self.old_base_cache_dir, self.id)
        if not os.path.exists(glob_repo_cache_dir):
            return False
        if os.path.normpath(glob_repo_cache_dir) == os.path.normpath(self.cachedir):
            return False

        # Try to copy whatever file it is
        fn = glob_repo_cache_dir   + '/' + subdir + os.path.basename(filename)
        if destfn is None:
            destfn = self.cachedir + '/' + subdir + os.path.basename(filename)
        return self._preload_file(fn, destfn)

    def _preload_md_from_system_cache(self, filename):
        """attempts to copy the metadata file from the system-wide cache,
           if possible"""
        return self._preload_file_from_system_cache(filename)
    
    def _preload_pkg_from_system_cache(self, pkg):
        """attempts to copy the package from the system-wide cache,
           if possible"""
        pname  = os.path.basename(pkg.localPkg())
        destfn = os.path.join(self.pkgdir, pname)
        if self._preload_file_from_system_cache(pkg.localPkg(),
                                                subdir='packages/',
                                                destfn=destfn):
            return True

        if not hasattr(self, '_old_pkgdirs'):
            return False
        for opkgdir in self._old_pkgdirs:
            if self._preload_file(os.path.join(opkgdir, pname), destfn):
                return True
        return False

    def _verify_md(self):
        problems = []
        print 'verifying md'
        try:
            md_types = self.repoXML.fileTypes()
        except Errors.RepoError, e:
            prb = RepoVerifyProblem(1, "failed to load repomd.xml", str(e))
            problems.append(prb)
            return problems

        for md_type in md_types:
            print 'verifying %s' % md_type
            try:
                self.retrieveMD(md_type)
            except Errors.RepoError, e:
                msg = "%s metadata missing or does not match checksum" % md_type
                prb = RepoVerifyProblem(2, msg, str(e))
                problems.append(prb)

        return problems

    def _verify_comps(self):
        print 'verifying comps'
        problems = []
        # grab the comps for this repo
        # run the xmllint on it
        # chuck it into a comps object
        # make sure it parses

        grpfile = self.getGroups()

        # open it up as a file object so iterparse can cope with our compressed file
        if grpfile is not None:
            grpfile = misc.decompress(grpfile)
        try:
            c = comps.Comps()
            c.add(grpfile)
        except (Errors.GroupsError, Errors.CompsException), e:
            msg = "comps file failed to add"
            prb = RepoVerifyProblem(REPO_PROBLEM_COMPS, msg, str(e))
            problems.add(prb)
        else:
            if c.compscount == 0:
                msg = "no groups in comps"
                prb = RepoVerifyProblem(REPO_PROBLEM_COMPS, msg, "")
                problems.add(prb)

        return problems

    def _verify_packages(self):
        return []

    def verify(self, items=['repodata', 'comps']):
        """download/verify the specified items
           @items = ['repodata', 'comps'] can include: repodata, comps, packages
        """
        problems = []
        if 'repodata' in items:
            problems.extend(self._verify_md())
        if 'comps' in items:        
            if self.enablegroups:
                problems.extend(self._verify_comps())
        if 'packages' in items:
            problems.extend(self._verify_packages())
        # what else can we verify?

        return problems


def getMirrorList(mirrorlist, pdict = None):
    warnings.warn('getMirrorList() will go away in a future version of Yum.\n',
            Errors.YumFutureDeprecationWarning, stacklevel=2)
    """retrieve an up2date-style mirrorlist file from a url,
       we also s/$ARCH/$BASEARCH/ and move along
       returns a list of the urls from that file"""

    returnlist = []
    if hasattr(urlgrabber.grabber, 'urlopen'):
        urlresolver = urlgrabber.grabber
    else:
        import urllib
        urlresolver = urllib

    scheme = urlparse.urlparse(mirrorlist)[0]
    if scheme == '':
        url = 'file://' + mirrorlist
    else:
        url = mirrorlist

    try:
        fo = urlresolver.urlopen(url, proxies=pdict)
    except URLGrabError, e:
        print "Could not retrieve mirrorlist %s error was\n%s: %s" % (url, e.args[0], misc.to_unicode(e.args[1]))
        fo = None

    if fo is not None:
        content = fo.readlines()
        for line in content:
            if re.match('\s*(#|$)', line):
                continue
            mirror = line.rstrip() # no more trailing \n's
            mirror = mirror.replace('$ARCH', '$BASEARCH')
            returnlist.append(mirror)

    return returnlist

class RepoVerifyProblem:
    """ Holder for each "problem" we find with a repo.verify(). """
    
    def __init__(self, type, msg, details, fake=False):
        self.type           = type
        self.message        = msg
        self.details        = details
        self.fake           = fake
