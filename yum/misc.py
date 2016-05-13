#! /usr/bin/python -tt
"""
Assorted utility functions for yum.
"""

import types
import os
import sys
import os.path
from cStringIO import StringIO
import base64
import binascii
import struct
import re
import errno
import Errors
import constants
import pgpmsg
import tempfile
import glob
import pwd
import fnmatch
import bz2
import gzip
import shutil
import urllib
import string
_available_compression = ['gz', 'bz2']
try:
    import lzma
    _available_compression.append('xz')
except ImportError:
    lzma = None

from rpmUtils.miscutils import stringToVersion, flagToString
from stat import *
try:
    import gpgme
    import gpgme.editutil
except ImportError:
    gpgme = None
try:
    import hashlib
    _available_checksums = set(['md5', 'sha1', 'sha256', 'sha384', 'sha512'])
except ImportError:
    # Python-2.4.z ... gah!
    import sha
    import md5
    _available_checksums = set(['md5', 'sha1'])
    class hashlib:

        @staticmethod
        def new(algo):
            if algo == 'md5':
                return md5.new()
            if algo == 'sha1':
                return sha.new()
            raise ValueError, "Bad checksum type"

# some checksum types might be disabled
for ctype in list(_available_checksums):
    try:
        hashlib.new(ctype)
    except:
        print >> sys.stderr, 'Checksum type %s disabled' % repr(ctype)
        _available_checksums.remove(ctype)
for ctype in 'sha256', 'sha1':
    if ctype in _available_checksums:
        _default_checksums = [ctype]
        break
else:
    raise ImportError, 'broken hashlib'

from Errors import MiscError
# These are API things, so we can't remove them even if they aren't used here.
# pylint: disable-msg=W0611
from i18n import to_utf8, to_unicode
# pylint: enable-msg=W0611

_share_data_store   = {}
_share_data_store_u = {}
def share_data(value):
    """ Take a value and use the same value from the store,
        if the value isn't in the store this one becomes the shared version. """
    #  We don't want to change the types of strings, between str <=> unicode
    # and hash('a') == hash(u'a') ... so use different stores.
    #  In theory eventaully we'll have all of one type, but don't hold breath.
    store = _share_data_store
    if isinstance(value, unicode):
        store = _share_data_store_u
    # hahahah, of course the above means that:
    #   hash(('a', 'b')) == hash((u'a', u'b'))
    # ...which we have in deptuples, so just screw sharing those atm.
    if type(value) == types.TupleType:
        return value
    return store.setdefault(value, value)

def unshare_data():
    global _share_data_store
    global _share_data_store_u
    _share_data_store   = {}
    _share_data_store_u = {}

_re_compiled_glob_match = None
def re_glob(s):
    """ Tests if a string is a shell wildcard. """
    # TODO/FIXME maybe consider checking if it is a stringsType before going on - otherwise
    # returning None
    global _re_compiled_glob_match
    if _re_compiled_glob_match is None:
        _re_compiled_glob_match = re.compile('[*?]|\[.+\]').search
    return _re_compiled_glob_match(s)

def compile_pattern(pat, ignore_case=False):
    """ Compile shell wildcards, return a 'match' function. """
    if re_glob(pat):
        try:
            flags = ignore_case and re.I or 0
            return re.compile(fnmatch.translate(pat), flags).match
        except re.error:
            pass # fall back to exact match
    if ignore_case:
        pat = pat.lower()
        return lambda s: s.lower() == pat
    return lambda s: s == pat

_re_compiled_filename_match = None
def re_filename(s):
    """ Tests if a string could be a filename. We still get negated character
        classes wrong (are they supported), and ranges in character classes. """
    global _re_compiled_filename_match
    if _re_compiled_filename_match is None:
        _re_compiled_filename_match = re.compile('[/*?]|\[[^]]*/[^]]*\]').match
    return _re_compiled_filename_match(s)

def re_primary_filename(filename):
    """ Tests if a filename string, can be matched against just primary.
        Note that this can produce false negatives (Eg. /b?n/zsh) but not false
        positives (because the former is a perf hit, and the later is a
        failure). Note that this is a superset of re_primary_dirname(). """
    if re_primary_dirname(filename):
        return True
    if filename == '/usr/lib/sendmail':
        return True
    return False

def re_primary_dirname(dirname):
    """ Tests if a dirname string, can be matched against just primary. Note
        that this is a subset of re_primary_filename(). """
    if 'bin/' in dirname:
        return True
    if dirname.startswith('/etc/'):
        return True
    return False

_re_compiled_full_match = None
def re_full_search_needed(s):
    """ Tests if a string needs a full nevra match, instead of just name. """
    global _re_compiled_full_match
    if _re_compiled_full_match is None:
        # A glob, or a "." or "-" separator, followed by something (the ".")
        one = re.compile('.*([-.*?]|\[.+\]).').match
        # Any epoch, for envra
        two = re.compile('[0-9]+:').match
        _re_compiled_full_match = (one, two)
    for rec in _re_compiled_full_match:
        if rec(s):
            return True
    return False

def re_remote_url(s):
    """ Tests if a string is a "remote" URL, http, https, ftp. """
    s = s.lower()
    if s.startswith("http://"):
        return True
    if s.startswith("https://"):
        return True
    if s.startswith("ftp://"):
        return True
    return False

###########
# Title: Remove duplicates from a sequence
# Submitter: Tim Peters 
# From: http://aspn.activestate.com/ASPN/Cookbook/Python/Recipe/52560
def unique(s):
    """Return a list of the elements in s, but without duplicates.

    For example, unique([1,2,3,1,2,3]) is some permutation of [1,2,3],
    unique("abcabc") some permutation of ["a", "b", "c"], and
    unique(([1, 2], [2, 3], [1, 2])) some permutation of
    [[2, 3], [1, 2]].

    For best speed, all sequence elements should be hashable.  Then
    unique() will usually work in linear time.

    If not possible, the sequence elements should enjoy a total
    ordering, and if list(s).sort() doesn't raise TypeError it's
    assumed that they do enjoy a total ordering.  Then unique() will
    usually work in O(N*log2(N)) time.

    If that's not possible either, the sequence elements must support
    equality-testing.  Then unique() will usually work in quadratic
    time.
    """

    n = len(s)
    if n == 0:
        return []

    # Try using a set first, as that's the fastest and will usually
    # work.  If it doesn't work, it will usually fail quickly, so it
    # usually doesn't cost much to *try* it.  It requires that all the
    # sequence elements be hashable, and support equality comparison.
    try:
        u = set(s)
    except TypeError:
        pass
    else:
        return list(u)

    # We can't hash all the elements.  Second fastest is to sort,
    # which brings the equal elements together; then duplicates are
    # easy to weed out in a single pass.
    # NOTE:  Python's list.sort() was designed to be efficient in the
    # presence of many duplicate elements.  This isn't true of all
    # sort functions in all languages or libraries, so this approach
    # is more effective in Python than it may be elsewhere.
    try:
        t = list(s)
        t.sort()
    except TypeError:
        del t  # move on to the next method
    else:
        assert n > 0
        last = t[0]
        lasti = i = 1
        while i < n:
            if t[i] != last:
                t[lasti] = last = t[i]
                lasti += 1
            i += 1
        return t[:lasti]

    # Brute force is all that's left.
    u = []
    for x in s:
        if x not in u:
            u.append(x)
    return u

class Checksums:
    """ Generate checksum(s), on given pieces of data. Producing the
        Length and the result(s) when complete. """

    def __init__(self, checksums=None, ignore_missing=False, ignore_none=False):
        if checksums is None:
            checksums = _default_checksums
        self._sumalgos = []
        self._sumtypes = []
        self._len = 0

        done = set()
        for sumtype in checksums:
            if sumtype == 'sha':
                sumtype = 'sha1'
            if sumtype in done:
                continue

            if sumtype in _available_checksums:
                sumalgo = hashlib.new(sumtype)
            elif ignore_missing:
                continue
            else:
                raise MiscError, 'Error Checksumming, bad checksum type %s' % sumtype
            done.add(sumtype)
            self._sumtypes.append(sumtype)
            self._sumalgos.append(sumalgo)
        if not done and not ignore_none:
            raise MiscError, 'Error Checksumming, no valid checksum type'

    def __len__(self):
        return self._len

    # Note that len(x) is assert limited to INT_MAX, which is 2GB on i686.
    length = property(fget=lambda self: self._len)

    def update(self, data):
        self._len += len(data)
        for sumalgo in self._sumalgos:
            sumalgo.update(data)

    def read(self, fo, size=2**16):
        data = fo.read(size)
        self.update(data)
        return data

    def hexdigests(self):
        ret = {}
        for sumtype, sumdata in zip(self._sumtypes, self._sumalgos):
            ret[sumtype] = sumdata.hexdigest()
        return ret

    def hexdigest(self, checksum=None):
        if checksum is None:
            if not self._sumtypes:
                return None
            checksum = self._sumtypes[0]
        if checksum == 'sha':
            checksum = 'sha1'
        return self.hexdigests()[checksum]

    def digests(self):
        ret = {}
        for sumtype, sumdata in zip(self._sumtypes, self._sumalgos):
            ret[sumtype] = sumdata.digest()
        return ret

    def digest(self, checksum=None):
        if checksum is None:
            if not self._sumtypes:
                return None
            checksum = self._sumtypes[0]
        if checksum == 'sha':
            checksum = 'sha1'
        return self.digests()[checksum]


class AutoFileChecksums:
    """ Generate checksum(s), on given file/fileobject. Pretending to be a file
        object (overrrides read). """

    def __init__(self, fo, checksums, ignore_missing=False, ignore_none=False):
        self._fo       = fo
        self.checksums = Checksums(checksums, ignore_missing, ignore_none)

    def __getattr__(self, attr):
        return getattr(self._fo, attr)

    def read(self, size=-1):
        return self.checksums.read(self._fo, size)


def checksum(sumtype, file, CHUNK=2**16, datasize=None):
    """takes filename, hand back Checksum of it
       sumtype = md5 or sha/sha1/sha256/sha512 (note sha == sha1)
       filename = /path/to/file
       CHUNK=65536 by default"""
     
    # chunking brazenly lifted from Ryan Tomayko
    try:
        if type(file) not in types.StringTypes:
            fo = file # assume it's a file-like-object
        else:           
            fo = open(file, 'r')

        data = Checksums([sumtype])
        while data.read(fo, CHUNK):
            if datasize is not None and data.length > datasize:
                break

        if type(file) is types.StringType:
            fo.close()
            
        # This screws up the length, but that shouldn't matter. We only care
        # if this checksum == what we expect.
        if datasize is not None and datasize != data.length:
            return '!%u!%s' % (datasize, data.hexdigest(sumtype))

        return data.hexdigest(sumtype)
    except (IOError, OSError), e:
        raise MiscError, 'Error opening file for checksum: %s' % file

def getFileList(path, ext, filelist):
    """Return all files in path matching ext, store them in filelist, 
       recurse dirs return list object"""
    
    extlen = len(ext)
    try:
        dir_list = os.listdir(path)
    except OSError, e:
        raise MiscError, ('Error accessing directory %s, %s') % (path, e)
        
    for d in dir_list:
        if os.path.isdir(path + '/' + d):
            filelist = getFileList(path + '/' + d, ext, filelist)
        else:
            if not ext or d[-extlen:].lower() == '%s' % (ext):
                newpath = os.path.normpath(path + '/' + d)
                filelist.append(newpath)
                    
    return filelist

class GenericHolder:
    """Generic Holder class used to hold other objects of known types
       It exists purely to be able to do object.somestuff, object.someotherstuff
       or object[key] and pass object to another function that will 
       understand it"""

    def __init__(self, iter=None):
        self.__iter = iter
       
    def __iter__(self):
        if self.__iter is not None:
            return iter(self[self.__iter])

    def __getitem__(self, item):
        if hasattr(self, item):
            return getattr(self, item)
        else:
            raise KeyError, item

def procgpgkey(rawkey):
    '''Convert ASCII armoured GPG key to binary
    '''
    # TODO: CRC checking? (will RPM do this anyway?)
    
    # Normalise newlines
    rawkey = re.sub('\r\n?', '\n', rawkey)

    # Extract block
    block = StringIO()
    inblock = 0
    pastheaders = 0
    for line in rawkey.split('\n'):
        if line.startswith('-----BEGIN PGP PUBLIC KEY BLOCK-----'):
            inblock = 1
        elif inblock and line.strip() == '':
            pastheaders = 1
        elif inblock and line.startswith('-----END PGP PUBLIC KEY BLOCK-----'):
            # Hit the end of the block, get out
            break
        elif pastheaders and line.startswith('='):
            # Hit the CRC line, don't include this and stop
            break
        elif pastheaders:
            block.write(line+'\n')
  
    # Decode and return
    return base64.decodestring(block.getvalue())

def gpgkey_fingerprint_ascii(info, chop=4):
    ''' Given a key_info data from getgpgkeyinfo(), return an ascii
    fingerprint. Chop every 4 ascii values, as that is what GPG does. '''
    # First "duh" ... it's a method...
    fp = info['fingerprint']()
    fp = binascii.hexlify(fp)
    if chop:
        fp = [fp[i:i+chop] for i in range(0, len(fp), chop)]
        fp = " ".join(fp)
    return fp

def getgpgkeyinfo(rawkey, multiple=False):
    '''Return a dict of info for the given ASCII armoured key text

    Returned dict will have the following keys: 'userid', 'keyid', 'timestamp'

    Will raise ValueError if there was a problem decoding the key.
    '''
    # Catch all exceptions as there can be quite a variety raised by this call
    key_info_objs = []
    try:
        keys = pgpmsg.decode_multiple_keys(rawkey)
    except Exception, e:
        raise ValueError(str(e))
    if len(keys) == 0:
        raise ValueError('No key found in given key data')
    
    for key in keys:    
        keyid_blob = key.public_key.key_id()

        info = {
            'userid': key.user_id,
            'keyid': struct.unpack('>Q', keyid_blob)[0],
            'timestamp': key.public_key.timestamp,
            'fingerprint' : key.public_key.fingerprint,
            'raw_key' : key.raw_key,
            'has_sig' : False,
            'valid_sig': False,
        }

        # Retrieve the timestamp from the matching signature packet 
        # (this is what RPM appears to do) 
        for userid in key.user_ids[0]:
            if not isinstance(userid, pgpmsg.signature):
                continue

            if userid.key_id() == keyid_blob:
                # Get the creation time sub-packet if available
                if hasattr(userid, 'hashed_subpaks'):
                    tspkt = \
                        userid.get_hashed_subpak(pgpmsg.SIG_SUB_TYPE_CREATE_TIME)
                    if tspkt != None:
                        info['timestamp'] = int(tspkt[1])
                        break
        key_info_objs.append(info)
    if multiple:      
        return key_info_objs
    else:
        return key_info_objs[0]
        

def keyIdToRPMVer(keyid):
    '''Convert an integer representing a GPG key ID to the hex version string
    used by RPM
    '''
    return "%08x" % (keyid & 0xffffffffL)


def keyInstalled(ts, keyid, timestamp):
    '''
    Return if the GPG key described by the given keyid and timestamp are
    installed in the rpmdb.  

    The keyid and timestamp should both be passed as integers.
    The ts is an rpm transaction set object

    Return values:
        - -1      key is not installed
        - 0       key with matching ID and timestamp is installed
        - 1       key with matching ID is installed but has a older timestamp
        - 2       key with matching ID is installed but has a newer timestamp

    No effort is made to handle duplicates. The first matching keyid is used to 
    calculate the return result.
    '''
    # Convert key id to 'RPM' form
    keyid = keyIdToRPMVer(keyid)

    # Search
    for hdr in ts.dbMatch('name', 'gpg-pubkey'):
        if hdr['version'] == keyid:
            installedts = int(hdr['release'], 16)
            if installedts == timestamp:
                return 0
            elif installedts < timestamp:
                return 1    
            else:
                return 2

    return -1

def import_key_to_pubring(rawkey, keyid, cachedir=None, gpgdir=None, make_ro_copy=True):
    # FIXME - cachedir can be removed from this method when we break api
    if gpgme is None:
        return False
    
    if not gpgdir:
        gpgdir = '%s/gpgdir' % cachedir
    
    if not os.path.exists(gpgdir):
        os.makedirs(gpgdir)
    
    key_fo = StringIO(rawkey) 
    os.environ['GNUPGHOME'] = gpgdir
    # import the key
    ctx = gpgme.Context()
    fp = open(os.path.join(gpgdir, 'gpg.conf'), 'wb')
    fp.write('')
    fp.close()
    ctx.import_(key_fo)
    key_fo.close()
    # ultimately trust the key or pygpgme is definitionally stupid
    k = ctx.get_key(keyid)
    gpgme.editutil.edit_trust(ctx, k, gpgme.VALIDITY_ULTIMATE)
    
    if make_ro_copy:

        rodir = gpgdir + '-ro'
        if not os.path.exists(rodir):
            os.makedirs(rodir, mode=0755)
            for f in glob.glob(gpgdir + '/*'):
                basename = os.path.basename(f)
                ro_f = rodir + '/' + basename
                shutil.copy(f, ro_f)
                os.chmod(ro_f, 0755)
            fp = open(rodir + '/gpg.conf', 'w', 0755)
            # yes it is this stupid, why do you ask?
            opts="""lock-never    
no-auto-check-trustdb    
trust-model direct
no-expensive-trust-checks
no-permission-warning         
preserve-permissions
"""
            fp.write(opts)
            fp.close()

        
    return True
    
def return_keyids_from_pubring(gpgdir):
    if gpgme is None or not os.path.exists(gpgdir):
        return []

    os.environ['GNUPGHOME'] = gpgdir
    ctx = gpgme.Context()
    keyids = []
    for k in ctx.keylist():
        for subkey in k.subkeys:
            if subkey.can_sign:
                keyids.append(subkey.keyid)

    return keyids

def valid_detached_sig(sig_file, signed_file, gpghome=None):
    """takes signature , file that was signed and an optional gpghomedir"""

    if gpgme is None:
        return False

    if gpghome:
        if not os.path.exists(gpghome):
            return False
        os.environ['GNUPGHOME'] = gpghome

    if hasattr(sig_file, 'read'):
        sig = sig_file
    else:
        sig = open(sig_file, 'r')
    if hasattr(signed_file, 'read'):
        signed_text = signed_file
    else:
        signed_text = open(signed_file, 'r')
    plaintext = None
    ctx = gpgme.Context()

    try:
        sigs = ctx.verify(sig, signed_text, plaintext)
    except gpgme.GpgmeError, e:
        return False
    else:
        if not sigs:
            return False
        # is there ever a case where we care about a sig beyond the first one?
        thissig = sigs[0]
        if not thissig:
            return False

        if thissig.validity in (gpgme.VALIDITY_FULL, gpgme.VALIDITY_MARGINAL,
                                gpgme.VALIDITY_ULTIMATE):
            return True

    return False

def getCacheDir(tmpdir='/var/tmp', reuse=True, prefix='yum-'):
    """return a path to a valid and safe cachedir - only used when not running
       as root or when --tempcache is set"""
    
    uid = os.geteuid()
    try:
        usertup = pwd.getpwuid(uid)
        username = usertup[0]
        # we prefer ascii-only paths
        username = urllib.quote(username)
    except KeyError:
        return None # if it returns None then, well, it's bollocksed

    if reuse:
        # check for /var/tmp/yum-username-* - 
        prefix = '%s%s-' % (prefix, username)
        dirpath = '%s/%s*' % (tmpdir, prefix)
        cachedirs = sorted(glob.glob(dirpath))
        for thisdir in cachedirs:
            stats = os.lstat(thisdir)
            if S_ISDIR(stats[0]) and S_IMODE(stats[0]) == 448 and stats[4] == uid:
                return thisdir

    # make the dir (tempfile.mkdtemp())
    cachedir = tempfile.mkdtemp(prefix=prefix, dir=tmpdir)
    return cachedir
        
def sortPkgObj(pkg1 ,pkg2):
    """sorts a list of yum package objects by name"""
    if pkg1.name > pkg2.name:
        return 1
    elif pkg1.name == pkg2.name:
        return 0
    else:
        return -1
        
def newestInList(pkgs):
    """ Return the newest in the list of packages. """
    ret = [ pkgs.pop() ]
    newest = ret[0]
    for pkg in pkgs:
        if pkg.verGT(newest):
            ret = [ pkg ]
            newest = pkg
        elif pkg.verEQ(newest):
            ret.append(pkg)
    return ret

def version_tuple_to_string(evrTuple):
    """
    Convert a tuple representing a package version to a string.

    @param evrTuple: A 3-tuple of epoch, version, and release.

    Return the string representation of evrTuple.
    """
    (e, v, r) = evrTuple
    s = ""
    
    if e not in [0, '0', None]:
        s += '%s:' % e
    if v is not None:
        s += '%s' % v
    if r is not None:
        s += '-%s' % r
    return s

def prco_tuple_to_string(prcoTuple):
    """returns a text string of the prco from the tuple format"""
    
    (name, flag, evr) = prcoTuple
    flags = {'GT':'>', 'GE':'>=', 'EQ':'=', 'LT':'<', 'LE':'<='}
    if flag is None:
        return name
    
    return '%s %s %s' % (name, flags[flag], version_tuple_to_string(evr))

def string_to_prco_tuple(prcoString):
    """returns a prco tuple (name, flags, (e, v, r)) for a string"""

    if type(prcoString) == types.TupleType:
        (n, f, v) = prcoString
    else:
        n = prcoString
        f = v = None
        
        # We love GPG keys as packages, esp. awesome provides like:
        #  gpg(Fedora (13) <fedora@fedoraproject.org>)
        if n[0] != '/' and not n.startswith("gpg("):
            # not a file dep - look at it for being versioned
            prco_split = n.split()
            if len(prco_split) == 3:
                n, f, v = prco_split
    
    # now we have 'n, f, v' where f and v could be None and None
    if f is not None and f not in constants.LETTERFLAGS:
        if f not in constants.SYMBOLFLAGS:
            try:
                f = flagToString(int(f))
            except (ValueError,TypeError), e:
                raise Errors.MiscError, 'Invalid version flag: %s' % f
        else:
            f = constants.SYMBOLFLAGS[f]

    if type(v) in (types.StringType, types.NoneType, types.UnicodeType):
        (prco_e, prco_v, prco_r) = stringToVersion(v)
    elif type(v) in (types.TupleType, types.ListType):
        (prco_e, prco_v, prco_r) = v
    
    #now we have (n, f, (e, v, r)) for the thing specified
    return (n, f, (prco_e, prco_v, prco_r))

def refineSearchPattern(arg):
    """Takes a search string from the cli for Search or Provides
       and cleans it up so it doesn't make us vomit"""
    
    if re.search('[*{}?+]|\[.+\]', arg):
        restring = fnmatch.translate(arg)
    else:
        restring = re.escape(arg)
        
    return restring


def _decompress_chunked(source, dest, ztype):

    if ztype not in _available_compression:
        msg = "%s compression not available" % ztype
        raise Errors.MiscError, msg
    
    if ztype == 'bz2':
        s_fn = bz2.BZ2File(source, 'r')
    elif ztype == 'xz':
        s_fn = lzma.LZMAFile(source, 'r')
    elif ztype == 'gz':
        s_fn = gzip.GzipFile(source, 'r')
    
    
    destination = open(dest, 'w')

    while True:
        try:
            data = s_fn.read(1024000)
        except (OSError, IOError, EOFError), e:
            msg = "Error reading from file %s: %s" % (source, str(e))
            raise Errors.MiscError, msg
        
        if not data: break

        try:
            destination.write(data)
        except (OSError, IOError), e:
            msg = "Error writing to file %s: %s" % (dest, str(e))
            raise Errors.MiscError, msg
    
    destination.close()
    s_fn.close()
    
def bunzipFile(source,dest):
    """ Extract the bzipped contents of source to dest. """
    _decompress_chunked(source, dest, ztype='bz2')
    
def get_running_kernel_pkgtup(ts):
    """This takes the output of uname and figures out the pkgtup of the running
       kernel (name, arch, epoch, version, release)."""
    ver = os.uname()[2]

    # we glob for the file that MIGHT have this kernel
    # and then look up the file in our rpmdb.
    fns = sorted(glob.glob('/boot/vmlinuz*%s*' % ver))
    for fn in fns:
        mi = ts.dbMatch('basenames', fn)
        for h in mi:
            e = h['epoch']
            if h['epoch'] is None:
                e = '0'
            else:
                e = str(e)
            return (h['name'], h['arch'], e, h['version'], h['release'])
    
    return (None, None, None, None, None)
 
def get_running_kernel_version_release(ts):
    """This takes the output of uname and figures out the (version, release)
    tuple for the running kernel."""
    pkgtup = get_running_kernel_pkgtup(ts)
    if pkgtup[0] is not None:
        return (pkgtup[3], pkgtup[4])
    return (None, None)

def find_unfinished_transactions(yumlibpath='/var/lib/yum'):
    """returns a list of the timestamps from the filenames of the unfinished 
       transactions remaining in the yumlibpath specified.
    """
    timestamps = []    
    tsallg = '%s/%s' % (yumlibpath, 'transaction-all*')
    tsdoneg = '%s/%s' % (yumlibpath, 'transaction-done*')
    tsalls = glob.glob(tsallg)
    tsdones = glob.glob(tsdoneg)

    for fn in tsalls:
        if fn.endswith('disabled'):
            continue
        trans = os.path.basename(fn)
        timestamp = trans.replace('transaction-all.','')
        timestamps.append(timestamp)

    timestamps.sort()
    return timestamps
    
def find_ts_remaining(timestamp, yumlibpath='/var/lib/yum'):
    """this function takes the timestamp of the transaction to look at and 
       the path to the yum lib dir (defaults to /var/lib/yum)
       returns a list of tuples(action, pkgspec) for the unfinished transaction
       elements. Returns an empty list if none.

    """
    
    to_complete_items = []
    tsallpath = '%s/%s.%s' % (yumlibpath, 'transaction-all', timestamp)    
    tsdonepath = '%s/%s.%s' % (yumlibpath,'transaction-done', timestamp)
    tsdone_items = []

    if not os.path.exists(tsallpath):
        # something is wrong, here, probably need to raise _something_
        return to_complete_items    

            
    if os.path.exists(tsdonepath):
        tsdone_fo = open(tsdonepath, 'r')
        tsdone_items = tsdone_fo.readlines()
        tsdone_fo.close()     
    
    tsall_fo = open(tsallpath, 'r')
    tsall_items = tsall_fo.readlines()
    tsall_fo.close()
    
    for item in tsdone_items:
        # this probably shouldn't happen but it's worth catching anyway
        if item not in tsall_items:
            continue        
        tsall_items.remove(item)
        
    for item in tsall_items:
        item = item.replace('\n', '')
        if item == '':
            continue
        try:
            (action, pkgspec) = item.split()
        except ValueError, e:
            msg = "Transaction journal  file %s is corrupt." % (tsallpath)
            raise Errors.MiscError, msg
        to_complete_items.append((action, pkgspec))
    
    return to_complete_items

def seq_max_split(seq, max_entries):
    """ Given a seq, split into a list of lists of length max_entries each. """
    ret = []
    num = len(seq)
    seq = list(seq) # Trying to use a set/etc. here is bad
    beg = 0
    while num > max_entries:
        end = beg + max_entries
        ret.append(seq[beg:end])
        beg += max_entries
        num -= max_entries
    ret.append(seq[beg:])
    return ret

_deletechars = ''.join(chr(i) for i in range(32) if i not in (9, 10, 13))

def to_xml(item, attrib=False):
    """ Returns xml-friendly utf-8 encoded string.
        Accepts utf-8, iso-8859-1, or unicode.
    """
    if type(item) is str:
        # check if valid utf8
        try: unicode(item, 'utf-8')
        except UnicodeDecodeError:
            # assume iso-8859-1
            item = unicode(item, 'iso-8859-1').encode('utf-8')
    elif type(item) is unicode:
        item = item.encode('utf-8')
    elif item is None:
        return ''
    else:
        raise ValueError, 'String expected, got %s' % repr(item)

    # compat cruft...
    item = item.rstrip()

    # kill ivalid low bytes
    item = item.translate(None, _deletechars)

    # quote reserved XML characters
    item = item.replace('&', '&amp;')
    item = item.replace('<', '&lt;')
    item = item.replace('>', '&gt;')
    if attrib:
        item = item.replace('"', '&quot;')
        item = item.replace("'", '&apos;')

    return item

def unlink_f(filename):
    """ Call os.unlink, but don't die if the file isn't there. This is the main
        difference between "rm -f" and plain "rm". """
    try:
        os.unlink(filename)
    except OSError, e:
        if e.errno not in (errno.ENOENT, errno.EPERM, errno.EACCES, errno.EROFS):
            raise

def stat_f(filename, ignore_EACCES=False):
    """ Call os.stat(), but don't die if the file isn't there. Returns None. """
    try:
        return os.stat(filename)
    except OSError, e:
        if e.errno in (errno.ENOENT, errno.ENOTDIR):
            return None
        if ignore_EACCES and e.errno == errno.EACCES:
            return None
        raise

def _getloginuid():
    """ Get the audit-uid/login-uid, if available. None is returned if there
        was a problem. Note that no caching is done here. """
    #  We might normally call audit.audit_getloginuid(), except that requires
    # importing all of the audit module. And it doesn't work anyway: BZ 518721
    try:
        fo = open("/proc/self/loginuid")
    except IOError:
        return None
    data = fo.read()
    try:
        return int(data)
    except ValueError:
        return None

_cached_getloginuid = None
def getloginuid():
    """ Get the audit-uid/login-uid, if available. None is returned if there
        was a problem. The value is cached, so you don't have to save it. """
    global _cached_getloginuid
    if _cached_getloginuid is None:
        _cached_getloginuid = _getloginuid()
    return _cached_getloginuid


# ---------- i18n ----------
import locale
def setup_locale(override_codecs=True, override_time=False):
    # This test needs to be before locale.getpreferredencoding() as that
    # does setlocale(LC_CTYPE, "")
    try:
        locale.setlocale(locale.LC_ALL, '')
        # set time to C so that we output sane things in the logs (#433091)
        if override_time:
            locale.setlocale(locale.LC_TIME, 'C')
    except locale.Error, e:
        # default to C locale if we get a failure.
        print >> sys.stderr, 'Failed to set locale, defaulting to C'
        os.environ['LC_ALL'] = 'C'
        locale.setlocale(locale.LC_ALL, 'C')
        
    if override_codecs:
        class UnicodeStream:
            def __init__(self, stream, encoding):
                self.stream = stream
                self.encoding = encoding
            def write(self, s):
                if isinstance(s, unicode):
                    s = s.encode(self.encoding, 'replace')
                self.stream.write(s)
            def __getattr__(self, name):
                return getattr(self.stream, name)
        sys.stdout = UnicodeStream(sys.stdout, locale.getpreferredencoding())


def get_my_lang_code():
    try:
        mylang = locale.getlocale(locale.LC_MESSAGES)
    except ValueError, e:
        # This is RHEL-5 python crack, Eg. en_IN can't be parsed properly
        mylang = (None, None)
    if mylang == (None, None): # odd :)
        mylang = 'C'
    else:
        mylang = '.'.join(mylang)
    
    return mylang
    
def return_running_pids():
    """return list of running processids, excluding this one"""
    mypid = os.getpid()
    pids = []
    for fn in glob.glob('/proc/[0123456789]*'):
        if mypid == os.path.basename(fn):
            continue
        pids.append(os.path.basename(fn))
    return pids

def get_open_files(pid):
    """returns files open from this pid"""
    files = []
    maps_f = '/proc/%s/maps' % pid
    try:
        maps = open(maps_f, 'r')
    except (IOError, OSError), e:
        return files

    for line in maps:
        if line.find('fd:') == -1:
            continue
        line = line.replace('\n', '')
        slash = line.find('/')
        filename = line[slash:]
        filename = filename.replace('(deleted)', '') #only mildly retarded
        filename = filename.strip()
        if filename not in files:
            files.append(filename)
    
    cli_f = '/proc/%s/cmdline' % pid
    try:
        cli = open(cli_f, 'r')
    except (IOError, OSError), e:
        return files
    
    cmdline = cli.read()
    if cmdline.find('\00') != -1:
        cmds = cmdline.split('\00')
        for i in cmds:
            if i.startswith('/'):
                files.append(i)

    return files

def get_uuid(savepath):
    """create, store and return a uuid. If a stored one exists, report that
       if it cannot be stored, return a random one"""
    if os.path.exists(savepath):
        return open(savepath, 'r').read()
    else:
        try:
            from uuid import uuid4
        except ImportError:
            myid = open('/proc/sys/kernel/random/uuid', 'r').read()
        else:
            myid = str(uuid4())
        
        try:
            sf = open(savepath, 'w')
            sf.write(myid)
            sf.flush()
            sf.close()
        except (IOError, OSError), e:
            pass
        
        return myid

class _Dynamic_UUID(object):
    def __init__(self, filename):
        self.filename = filename
        self.uuid = None

    def __str__(self):
        if self.uuid is None:
            self.uuid = get_uuid(self.filename)
        return self.uuid

    def __unicode__(self):
        return to_unicode(self.__str__())

def get_uuid_obj(savepath):
    """ Like get_uuid() but doesn't create the uuid file until it's needed. """
    return _Dynamic_UUID(savepath)
        
def decompress(filename, dest=None, fn_only=False, check_timestamps=False):
    """take a filename and decompress it into the same relative location.
       if the file is not compressed just return the file"""
    
    out = dest
    if not dest:
        out = filename
        
    if filename.endswith('.gz'):
        ztype='gz'
        if not dest: 
            out = filename.replace('.gz', '')

    elif filename.endswith('.bz') or filename.endswith('.bz2'):
        ztype='bz2'
        if not dest:
            if filename.endswith('.bz'):
                out = filename.replace('.bz','')
            else:
                out = filename.replace('.bz2', '')
    
    elif filename.endswith('.xz'):
        ztype='xz'
        if not dest:
            out = filename.replace('.xz', '')
        
    else:
        return filename # returning the same file since it is not compressed
    
    if check_timestamps:
        fi = stat_f(filename)
        fo = stat_f(out)
        if fi and fo:
            # Eliminate sub second precision in mtime before comparison,
            # see http://bugs.python.org/issue14127
            if int(fo.st_mtime) == int(fi.st_mtime):
                return out
            if fn_only:
                # out exists but not valid
                return None

    if not fn_only:
        try:
            _decompress_chunked(filename, out, ztype)
            if check_timestamps and fi:
                os.utime(out, (fi.st_mtime, fi.st_mtime))
        except:
            unlink_f(out)
            raise
        
    return out
    
def repo_gen_decompress(filename, generated_name, cached=False):
    """ This is a wrapper around decompress, where we work out a cached
        generated name, and use check_timestamps. filename _must_ be from
        a repo. and generated_name is the type of the file. """
    dest = os.path.dirname(filename) + '/gen/' + generated_name
    try:
        return decompress(filename, dest=dest, check_timestamps=True)
    except (OSError, IOError), e:
        if cached and e.errno == errno.EACCES:
            return None
        raise

def read_in_items_from_dot_dir(thisglob, line_as_list=True):
    """takes a glob of a dir (like /etc/foo.d/*.foo)
       returns a list of all the lines in all the files matching
       that glob, ignores comments and blank lines,
       optional paramater 'line_as_list tells whether to
       treat each line as a space or comma-separated list, defaults to True"""
    results = []
    for fname in glob.glob(thisglob):
        for line in open(fname):
            if re.match('\s*(#|$)', line):
                continue
            line = line.rstrip() # no more trailing \n's
            line = line.lstrip() # be nice
            if not line:
                continue
            if line_as_list:
                line = line.replace('\n', ' ')
                line = line.replace(',', ' ')
                results.extend(line.split())
                continue
            results.append(line)
    return results

__cached_cElementTree = None
def _cElementTree_import():
    """ Importing xElementTree all the time, when we often don't need it, is a
        huge timesink. This makes python -c 'import yum' suck. So we hide it
        behind this function. And have accessors. """
    global __cached_cElementTree
    if __cached_cElementTree is None:
        try:
            from xml.etree import cElementTree
        except ImportError:
            import cElementTree
        __cached_cElementTree = cElementTree

def cElementTree_iterparse(filename):
    """ Lazily load/run: cElementTree.iterparse """
    _cElementTree_import()
    return __cached_cElementTree.iterparse(filename)

def cElementTree_xmlparse(filename):
    """ Lazily load/run: cElementTree.parse """
    _cElementTree_import()
    return __cached_cElementTree.parse(filename)

def filter_pkgs_repoid(pkgs, repoid):
    """ Given a list of packages, filter them for those "in" the repoid.
    uses from_repo for installed packages, used by repo-pkgs commands. """

    if repoid is None:
        return pkgs

    ret = []
    for pkg in pkgs:
        if pkg.repoid == 'installed':
            if 'from_repo' not in pkg.yumdb_info:
                continue
            if pkg.yumdb_info.from_repo != repoid:
                continue
        elif pkg.repoid != repoid:
            continue
        ret.append(pkg)
    return ret

def validate_repoid(repoid):
    """Return the first invalid char found in the repoid, or None."""
    allowed_chars = string.ascii_letters + string.digits + '-_.:'
    for char in repoid:
        if char not in allowed_chars:
            return char
    else:
        return None

def split_advisory(id_):
    """Split the advisory ID into the root ID and respin suffix."""

    # Regexes for different advisory formats
    patterns = [
        r'^(RH[BES]A\-\d+\:\d+)(?:\-(\d+))?$',  # Red Hat errata
    ]

    for pat in patterns:
        match = re.match(pat, id_)
        if match:
            return match.groups()
    else:
        return id_, None
