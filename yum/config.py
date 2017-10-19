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
# Copyright 2002 Duke University 

"""
Configuration parser and default values for yum.
"""
_use_iniparse = True

import os
import sys
import warnings
import rpm
import copy
import urlparse
import shlex
from parser import ConfigPreProcessor, varReplace
try:
    from iniparse import INIConfig
    from iniparse.compat import NoSectionError, NoOptionError, ParsingError
    from iniparse.compat import RawConfigParser as ConfigParser
except ImportError:
    _use_iniparse = False
if not _use_iniparse:
    from ConfigParser import NoSectionError, NoOptionError, ParsingError
    from ConfigParser import ConfigParser
import rpmUtils.transaction
import rpmUtils.miscutils
import Errors
import types
from misc import get_uuid_obj, read_in_items_from_dot_dir

# Alter/patch these to change the default checking...
__pkgs_gpgcheck_default__ = False
__repo_gpgcheck_default__ = False
__main_multilib_policy_default__ = 'all'
__main_failovermethod_default__ = 'roundrobin'
__main_installonly_limit_default__ = 0
__group_command_default__ = 'compat'
__exactarchlist_default__ = ['kernel', 'kernel-smp',
                             'kernel-hugemem', 'kernel-enterprise',
                             'kernel-bigmem',
                             'kernel-devel', 'kernel-PAE', 'kernel-PAE-debug']

class Option(object):
    """
    This class handles a single Yum configuration file option. Create
    subclasses for each type of supported configuration option.
    Python descriptor foo (__get__ and __set__) is used to make option
    definition easy and concise.
    """

    def __init__(self, default=None, parse_default=False):
        self._setattrname()
        self.inherit = False
        if parse_default:
            default = self.parse(default)
        self.default = default

    def _setattrname(self):
        """Calculate the internal attribute name used to store option state in
        configuration instances.
        """
        self._attrname = '__opt%d' % id(self)

    def __get__(self, obj, objtype):
        """Called when the option is read (via the descriptor protocol). 

        :param obj: The configuration instance to modify.
        :param objtype: The type of the config instance (not used).
        :return: The parsed option value or the default value if the value
           wasn't set in the configuration file.
        """
        # xemacs highlighting hack: '
        if obj is None:
            return self

        return getattr(obj, self._attrname, None)

    def __set__(self, obj, value):
        """Called when the option is set (via the descriptor protocol). 

        :param obj: The configuration instance to modify.
        :param value: The value to set the option to.
        """
        # Only try to parse if it's a string
        if isinstance(value, basestring):
            try:
                value = self.parse(value)
            except ValueError, e:
                # Add the field name onto the error
                raise ValueError('Error parsing "%s = %r": %s' % (self._optname,
                                                                 value, str(e)))
        setattr(obj, self._attrname, value)

    def setup(self, obj, name):
        """Initialise the option for a config instance. 
        This must be called before the option can be set or retrieved. 

        :param obj: :class:`BaseConfig` (or subclass) instance.
        :param name: Name of the option.
        """
        self._optname = name
        setattr(obj, self._attrname, copy.copy(self.default))

    def clone(self):
        """Return a safe copy of this :class:`Option` instance.

        :return: a safe copy of this :class:`Option` instance
        """
        new = copy.copy(self)
        new._setattrname()
        return new

    def parse(self, s):
        """Parse the string value to the :class:`Option`'s native value.

        :param s: raw string value to parse
        :return: validated native value
        :raise: ValueError if there was a problem parsing the string.
           Subclasses should override this
        """
        return s

    def tostring(self, value):
        """Convert the :class:`Option`'s native value to a string value.  This
        does the opposite of the :func:`parse` method above.
        Subclasses should override this.

        :param value: native option value
        :return: string representation of input
        """
        return str(value)

def Inherit(option_obj):
    """Clone an :class:`Option` instance for the purposes of inheritance. The returned
    instance has all the same properties as the input :class:`Option` and shares items
    such as the default value. Use this to avoid redefinition of reused
    options.

    :param option_obj: :class:`Option` instance to inherit
    :return: New :class:`Option` instance inherited from the input
    """
    new_option = option_obj.clone()
    new_option.inherit = True
    return new_option

class ListOption(Option):
    """An option containing a list of strings."""

    def __init__(self, default=None, parse_default=False):
        if default is None:
            default = []
        super(ListOption, self).__init__(default, parse_default)

    def parse(self, s):
        """Convert a string from the config file to a workable list, parses
        globdir: paths as foo.d-style dirs.

        :param s: The string to be converted to a list. Commas and
           whitespace are used as separators for the list
        :return: *s* converted to a list
        """
        # we need to allow for the '\n[whitespace]' continuation - easier
        # to sub the \n with a space and then read the lines
        s = s.replace('\n', ' ')
        s = s.replace(',', ' ')
        results = []
        for item in s.split():
            if item.startswith('glob:'):
                thisglob = item.replace('glob:', '')
                results.extend(read_in_items_from_dot_dir(thisglob))
                continue
            results.append(item)

        return results

    def tostring(self, value):
        """Convert a list of to a string value.  This does the
        opposite of the :func:`parse` method above.

        :param value: a list of values
        :return: string representation of input
        """
        return '\n '.join(value)

class UrlOption(Option):
    """This option handles lists of URLs with validation of the URL
    scheme.
    """

    def __init__(self, default=None, schemes=('http', 'ftp', 'file', 'https'), 
            allow_none=False):
        super(UrlOption, self).__init__(default)
        self.schemes = schemes
        self.allow_none = allow_none

    def parse(self, url):
        """Parse a url to make sure that it is valid, and in a scheme
        that can be used.

        :param url: a string containing the url to parse
        :return: *url* if it is valid
        :raises: :class:`ValueError` if there is an error parsing the url
        """
        url = url.strip()

        # Handle the "_none_" special case
        if url.lower() == '_none_':
            if self.allow_none:
                return None
            else:
                raise ValueError('"_none_" is not a valid value')

        # Check that scheme is valid
        (s,b,p,q,f,o) = urlparse.urlparse(url)
        if s not in self.schemes:
            raise ValueError('URL must be %s not "%s"' % (self._schemelist(), s))

        return url

    def _schemelist(self):
        '''Return a user friendly list of the allowed schemes
        '''
        if len(self.schemes) < 1:
            return 'empty'
        elif len(self.schemes) == 1:
            return self.schemes[0]
        else:
            return '%s or %s' % (', '.join(self.schemes[:-1]), self.schemes[-1])

class ProxyOption(UrlOption):
    """ Just like URLOption but accept "libproxy" too.
    """
    def parse(self, proxy):
        if proxy.strip().lower() == 'libproxy':
            return 'libproxy'
        return UrlOption.parse(self, proxy)

class UrlListOption(ListOption):
    """Option for handling lists of URLs with validation of the URL
    scheme.
    """
    def __init__(self, default=None, schemes=('http', 'ftp', 'file', 'https'),
                 parse_default=False):
        super(UrlListOption, self).__init__(default, parse_default)

        # Hold a UrlOption instance to assist with parsing
        self._urloption = UrlOption(schemes=schemes)
        
    def parse(self, s):
        """Parse a string containing multiple urls into a list, and
        ensure that they are in a scheme that can be used.

        :param s: the string to parse
        :return: a list of strings containing the urls in *s*
        :raises: :class:`ValueError` if there is an error parsing the urls
        """
        out = []
        s = s.replace('\n', ' ')
        s = s.replace(',', ' ')
        items = [ item.replace(' ', '%20') for item in shlex.split(s) ]
        tmp = []
        for item in items:
            if item.startswith('glob:'):
                thisglob = item.replace('glob:', '')
                tmp.extend(read_in_items_from_dot_dir(thisglob))
                continue
            tmp.append(item)

        for url in super(UrlListOption, self).parse(' '.join(tmp)):
            out.append(self._urloption.parse(url))
        return out


class IntOption(Option):
    """An option representing an integer value."""

    def __init__(self, default=None, range_min=None, range_max=None):
        super(IntOption, self).__init__(default)
        self._range_min = range_min
        self._range_max = range_max
        
    def parse(self, s):
        """Parse a string containing an integer.

        :param s: the string to parse
        :return: the integer in *s*
        :raises: :class:`ValueError` if there is an error parsing the
           integer
        """
        try:
            val = int(s)
        except (ValueError, TypeError), e:
            raise ValueError('invalid integer value')
        if self._range_max is not None and val > self._range_max:
            raise ValueError('out of range integer value')
        if self._range_min is not None and val < self._range_min:
            raise ValueError('out of range integer value')
        return val

class PositiveIntOption(IntOption):
    """An option representing a positive integer value, where 0 can
    have a special representation.
    """
    def __init__(self, default=None, range_min=0, range_max=None,
                 names_of_0=None):
        super(PositiveIntOption, self).__init__(default, range_min, range_max)
        self._names0 = names_of_0

    def parse(self, s):
        """Parse a string containing a positive integer, where 0 can
           have a special representation.

        :param s: the string to parse
        :return: the integer in *s*
        :raises: :class:`ValueError` if there is an error parsing the
           integer
        """
        if s in self._names0:
            return 0
        return super(PositiveIntOption, self).parse(s)

class SecondsOption(Option):
    """An option representing an integer value of seconds, or a human
    readable variation specifying days, hours, minutes or seconds
    until something happens. Works like :class:`BytesOption`.  Note
    that due to historical president -1 means "never", so this accepts
    that and allows the word never, too.

    Valid inputs: 100, 1.5m, 90s, 1.2d, 1d, 0xF, 0.1, -1, never.
    Invalid inputs: -10, -0.1, 45.6Z, 1d6h, 1day, 1y.

    Return value will always be an integer
    """
    MULTS = {'d': 60 * 60 * 24, 'h' : 60 * 60, 'm' : 60, 's': 1}

    def parse(self, s):
        """Parse a string containing an integer value of seconds, or a human
        readable variation specifying days, hours, minutes or seconds
        until something happens. Works like :class:`BytesOption`.  Note
        that due to historical president -1 means "never", so this accepts
        that and allows the word never, too.
    
        Valid inputs: 100, 1.5m, 90s, 1.2d, 1d, 0xF, 0.1, -1, never.
        Invalid inputs: -10, -0.1, 45.6Z, 1d6h, 1day, 1y.
    
        :param s: the string to parse
        :return: an integer representing the number of seconds
           specified by *s*
        :raises: :class:`ValueError` if there is an error parsing the string
        """
        if len(s) < 1:
            raise ValueError("no value specified")

        if s == "-1" or s == "never": # Special cache timeout, meaning never
            return -1
        if s[-1].isalpha():
            n = s[:-1]
            unit = s[-1].lower()
            mult = self.MULTS.get(unit, None)
            if not mult:
                raise ValueError("unknown unit '%s'" % unit)
        else:
            n = s
            mult = 1

        try:
            n = float(n)
        except (ValueError, TypeError), e:
            raise ValueError('invalid value')

        if n < 0:
            raise ValueError("seconds value may not be negative")

        return int(n * mult)

class BoolOption(Option):
    """An option representing a boolean value.  The value can be one
    of 0, 1, yes, no, true, or false.
    """

    def parse(self, s):
        """Parse a string containing a boolean value.  1, yes, and
        true will evaluate to True; and 0, no, and false will evaluate
        to False.  Case is ignored.
        
        :param s: the string containing the boolean value
        :return: the boolean value contained in *s*
        :raises: :class:`ValueError` if there is an error in parsing
           the boolean value
        """
        s = s.lower()
        if s in ('0', 'no', 'false'):
            return False
        elif s in ('1', 'yes', 'true'):
            return True
        else:
            raise ValueError('invalid boolean value')

    def tostring(self, value):
        """Convert a boolean value to a string value.  This does the
        opposite of the :func:`parse` method above.
        
        :param value: the boolean value to convert
        :return: a string representation of *value*
        """
        if value:
            return "1"
        else:
            return "0"

class FloatOption(Option):
    """An option representing a numeric float value."""

    def parse(self, s):
        """Parse a string containing a numeric float value.

        :param s: a string containing a numeric float value to parse
        :return: the numeric float value contained in *s*
        :raises: :class:`ValueError` if there is an error parsing
           float value
        """
        try:
            return float(s.strip())
        except (ValueError, TypeError):
            raise ValueError('invalid float value')

class SelectionOption(Option):
    """Handles string values where only specific values are
    allowed.
    """
    def __init__(self, default=None, allowed=(), mapper={}):
        super(SelectionOption, self).__init__(default)
        self._allowed = allowed
        self._mapper  = mapper
        
    def parse(self, s):
        """Parse a string for specific values.

        :param s: the string to parse
        :return: *s* if it contains a valid value
        :raises: :class:`ValueError` if there is an error parsing the values
        """
        if s in self._mapper:
            s = self._mapper[s]
        if s not in self._allowed:
            raise ValueError('"%s" is not an allowed value' % s)
        return s

class CaselessSelectionOption(SelectionOption):
    """Mainly for compatibility with :class:`BoolOption`, works like
    :class:`SelectionOption` but lowers input case.
    """
    def parse(self, s):
        """Parse a string for specific values.

        :param s: the string to parse
        :return: *s* if it contains a valid value
        :raises: :class:`ValueError` if there is an error parsing the values
        """
        return super(CaselessSelectionOption, self).parse(s.lower())

class BytesOption(Option):
    """An option representing a value in bytes. The value may be given
    in bytes, kilobytes, megabytes, or gigabytes.
    """
    # Multipliers for unit symbols
    MULTS = {
        'k': 1024,
        'm': 1024*1024,
        'g': 1024*1024*1024,
    }

    def parse(self, s):
        """Parse a friendly bandwidth option to bytes.  The input
        should be a string containing a (possibly floating point)
        number followed by an optional single character unit. Valid
        units are 'k', 'M', 'G'. Case is ignored. The convention that
        1k = 1024 bytes is used.
       
        Valid inputs: 100, 123M, 45.6k, 12.4G, 100K, 786.3, 0.
        Invalid inputs: -10, -0.1, 45.6L, 123Mb.

        :param s: the string to parse
        :return: the number of bytes represented by *s*
        :raises: :class:`ValueError` if the option can't be parsed
        """
        if len(s) < 1:
            raise ValueError("no value specified")

        if s[-1].isalpha():
            n = s[:-1]
            unit = s[-1].lower()
            mult = self.MULTS.get(unit, None)
            if not mult:
                raise ValueError("unknown unit '%s'" % unit)
        else:
            n = s
            mult = 1
             
        try:
            n = float(n)
        except ValueError:
            raise ValueError("couldn't convert '%s' to number" % n)

        if n < 0:
            raise ValueError("bytes value may not be negative")

        return int(n * mult)

class ThrottleOption(BytesOption):
    """An option representing a bandwidth throttle value. See
    :func:`parse` for acceptable input values.
    """

    def parse(self, s):
        """Get a throttle option. Input may either be a percentage or
        a "friendly bandwidth value" as accepted by the
        :class:`BytesOption`.

        Valid inputs: 100, 50%, 80.5%, 123M, 45.6k, 12.4G, 100K, 786.0, 0.
        Invalid inputs: 100.1%, -4%, -500.

        :param s: the string to parse
        :return: the bandwidth represented by *s*. The return value
           will be an int if a bandwidth value was specified, and a
           float if a percentage was given
        :raises: :class:`ValueError` if input can't be parsed
        """
        if len(s) < 1:
            raise ValueError("no value specified")

        if s[-1] == '%':
            n = s[:-1]
            try:
                n = float(n)
            except ValueError:
                raise ValueError("couldn't convert '%s' to number" % n)
            if n < 0 or n > 100:
                raise ValueError("percentage is out of range")
            return n / 100.0
        else:
            return BytesOption.parse(self, s)

class BaseConfig(object):
    """Base class for storing configuration definitions. Subclass when
    creating your own definitions.
    """

    def __init__(self):
        self._section = None

        for name in self.iterkeys():
            option = self.optionobj(name)
            option.setup(self, name)

    def __str__(self):
        out = []
        out.append('[%s]' % self._section)
        for name, value in self.iteritems():
            out.append('%s: %r' % (name, value))
        return '\n'.join(out)

    def populate(self, parser, section, parent=None):
        """Set option values from an INI file section.

        :param parser: :class:`ConfigParser` instance (or subclass)
        :param section: INI file section to read use
        :param parent: Optional parent :class:`BaseConfig` (or
            subclass) instance to use when doing option value
            inheritance
        """
        self.cfg = parser
        self._section = section

        if parser.has_section(section):
            opts = set(parser.options(section))
        else:
            opts = set()
        for name in self.iterkeys():
            option = self.optionobj(name)
            value = None
            if name in opts:
                value = parser.get(section, name)
            else:
                # No matching option in this section, try inheriting
                if parent and option.inherit:
                    value = getattr(parent, name)
               
            if value is not None:
                setattr(self, name, value)

    def optionobj(cls, name, exceptions=True):
        """Return the :class:`Option` instance for the given name.

        :param cls: the class to return the :class:`Option` instance from
        :param name: the name of the :class:`Option` instance to return
        :param exceptions: defines what action to take if the
           specified :class:`Option` instance does not exist. If *exceptions* is
           True, a :class:`KeyError` will be raised. If *exceptions*
           is False, None will be returned
        :return: the :class:`Option` instance specified by *name*, or None if
           it does not exist and *exceptions* is False
        :raises: :class:`KeyError` if the specified :class:`Option` does not
           exist, and *exceptions* is True
        """
        obj = getattr(cls, name, None)
        if isinstance(obj, Option):
            return obj
        elif exceptions:
            raise KeyError
        else:
            return None
    optionobj = classmethod(optionobj)

    def isoption(cls, name):
        """Return True if the given name refers to a defined option.

        :param cls: the class to find the option in
        :param name: the name of the option to search for
        :return: whether *name* specifies a defined option
        """
        return cls.optionobj(name, exceptions=False) is not None
    isoption = classmethod(isoption)

    def iterkeys(self):
        """Yield the names of all defined options in the instance."""

        for name in dir(self):
            if self.isoption(name):
                yield name

    def iteritems(self):
        """Yield (name, value) pairs for every option in the
        instance. The value returned is the parsed, validated option
        value.
        """
        # Use dir() so that we see inherited options too
        for name in self.iterkeys():
            yield (name, getattr(self, name))

    def write(self, fileobj, section=None, always=()):
        """Write out the configuration to a file-like object.

        :param fileobj: File-like object to write to
        :param section: Section name to use. If not specified, the section name
            used during parsing will be used
        :param always: A sequence of option names to always write out.
            Options not listed here will only be written out if they are at
            non-default values. Set to None to dump out all options
        """
        # Write section heading
        if section is None:
            if self._section is None:
                raise ValueError("not populated, don't know section")
            section = self._section

        # Updated the ConfigParser with the changed values    
        cfgOptions = self.cfg.options(section)
        for name,value in self.iteritems():
            option = self.optionobj(name)
            if always is None or name in always or option.default != value or name in cfgOptions :
                self.cfg.set(section,name, option.tostring(value))
        # write the updated ConfigParser to the fileobj.
        self.cfg.write(fileobj)

    def getConfigOption(self, option, default=None):
        """Return the current value of the given option.

        :param option: string specifying the option to return the
           value of
        :param default: the value to return if the option does not exist
        :return: the value of the option specified by *option*, or
           *default* if it does not exist
        """
        warnings.warn('getConfigOption() will go away in a future version of Yum.\n'
                'Please access option values as attributes or using getattr().',
                DeprecationWarning)
        if hasattr(self, option):
            return getattr(self, option)
        return default

    def setConfigOption(self, option, value):
        """Set the value of the given option to the given value.

        :param option: string specifying the option to set the value
           of
        :param value: the value to set the option to
        """        
        warnings.warn('setConfigOption() will go away in a future version of Yum.\n'
                'Please set option values as attributes or using setattr().',
                DeprecationWarning)
        if hasattr(self, option):
            setattr(self, option, value)
        else:
            raise Errors.ConfigError, 'No such option %s' % option

class StartupConf(BaseConfig):
    """Configuration option definitions for yum.conf's [main] section
    that are required early in the initialisation process or before
    the other [main] options can be parsed.
    """
    # xemacs highlighting hack: '
    debuglevel = IntOption(2, -4, 10)
    errorlevel = IntOption(2, 0, 10)

    distroverpkg = ListOption(['system-release(releasever)', 'redhat-release'])
    installroot = Option('/')
    config_file_path = Option('/etc/yum/yum.conf')
    plugins = BoolOption(False)
    pluginpath = ListOption(['/usr/share/yum-plugins', '/usr/lib/yum-plugins'])
    pluginconfpath = ListOption(['/etc/yum/pluginconf.d'])
    gaftonmode = BoolOption(False)
    syslog_ident = Option()
    syslog_facility = Option('LOG_USER')
    syslog_device = Option('/dev/log')
    persistdir = Option('/var/lib/yum')
    skip_missing_names_on_install = BoolOption(True)
    skip_missing_names_on_update = BoolOption(True)
    
class YumConf(StartupConf):
    """Configuration option definitions for yum.conf's [main] section.

    Note: see also options inherited from :class:`StartupConf`
    """
    retries = PositiveIntOption(10, names_of_0=["<forever>"])
    recent = IntOption(7, range_min=0)
    reset_nice = BoolOption(True)

    cachedir = Option('/var/cache/yum')

    #  Name it differently from above, to avoid confusion.
    #  Also probably not a good idea to change this anyway, much like above,
    # so don't name it small.
    cashe_root_dir = Option('/var/cache/CAShe')

    keepcache = BoolOption(True)
    usercache = BoolOption(True)
    logfile = Option('/var/log/yum.log')
    reposdir = ListOption(['/etc/yum/repos.d', '/etc/yum.repos.d'])

    commands = ListOption()
    exclude = ListOption()
    failovermethod = Option(__main_failovermethod_default__)
    proxy = ProxyOption(default=False, schemes=('http', 'ftp', 'https',
        'socks4', 'socks4a', 'socks5', 'socks5h'), allow_none=True)
    proxy_username = Option()
    proxy_password = Option()
    username = Option()
    password = Option()
    installonlypkgs = ListOption(['kernel',
                                  'kernel-devel',
                                  'kernel-source',
                                  'installonlypkg(kernel)',
                                  'installonlypkg(kernel-module)',
                                  'installonlypkg(vm)'])
    # NOTE: If you set this to 2, then because it keeps the current kernel it
    # means if you ever install an "old" kernel it'll get rid of the newest one
    # so you probably want to use 3 as a minimum ... if you turn it on.
    installonly_limit = PositiveIntOption(__main_installonly_limit_default__,
                                          range_min=2,
                                          names_of_0=["0", "<off>"])
    kernelpkgnames = ListOption(['kernel','kernel-smp', 'kernel-enterprise',
            'kernel-bigmem', 'kernel-BOOT', 'kernel-PAE', 'kernel-PAE-debug'])
    exactarchlist = ListOption(__exactarchlist_default__)
    tsflags = ListOption()
    override_install_langs = Option()

    assumeyes = BoolOption(False)
    assumeno  = BoolOption(False)
    alwaysprompt = BoolOption(True)
    exactarch = BoolOption(True)
    tolerant = BoolOption(True)
    diskspacecheck = BoolOption(True)
    overwrite_groups = BoolOption(False)
    keepalive = BoolOption(True)
    # FIXME: rename gpgcheck to pkgs_gpgcheck
    gpgcheck = BoolOption(__pkgs_gpgcheck_default__)
    repo_gpgcheck = BoolOption(__repo_gpgcheck_default__)
    localpkg_gpgcheck = BoolOption(__pkgs_gpgcheck_default__)
    obsoletes = BoolOption(True)
    showdupesfromrepos = BoolOption(False)
    enabled = BoolOption(True)
    remove_leaf_only = BoolOption(False)
    repopkgsremove_leaf_only = BoolOption(False)
    enablegroups = BoolOption(True)
    enable_group_conditionals = BoolOption(True)
    groupremove_leaf_only = BoolOption(False)
    group_package_types = ListOption(['mandatory', 'default'])
    group_command = SelectionOption(__group_command_default__,
                                    ('compat', 'objects', 'simple'))
    upgrade_group_objects_upgrade = BoolOption(True)
    
    timeout = FloatOption(30.0) # FIXME: Should use variation of SecondsOption

    minrate = IntOption(0)
    bandwidth = BytesOption(0)
    throttle = ThrottleOption(0)
    ip_resolve = CaselessSelectionOption(
            allowed = ('ipv4', 'ipv6', 'whatever'),
            mapper  = {'4': 'ipv4', '6': 'ipv6'})
    max_connections = IntOption(0, range_min=0)
    ftp_disable_epsv = BoolOption(False)
    deltarpm = IntOption(2, range_min=-16, range_max=128)
    deltarpm_percentage = IntOption(75, range_min=0, range_max=100)
    deltarpm_metadata_percentage = IntOption(100, range_min=0)

    http_caching = SelectionOption('all', ('none', 'packages', 'all',
                                           'lazy:packages'))
    metadata_expire = SecondsOption(60 * 60 * 6) # Time in seconds (6h).
    metadata_expire_filter = SelectionOption('read-only:present',
                                             ('never', 'read-only:future',
                                              'read-only:present',
                                              'read-only:past'))
    # Time in seconds (1 day). NOTE: This isn't used when using metalinks
    mirrorlist_expire = SecondsOption(60 * 60 * 24)
    # XXX rpm_check_debug is unused, left around for API compatibility for now
    rpm_check_debug = BoolOption(True)
    disable_excludes = ListOption()    
    query_install_excludes = BoolOption(False)
    skip_broken = BoolOption(False)
    #  Note that "instant" is the old behaviour, but group:primary is very
    # similar but better :).
    mdpolicy = ListOption(['group:small'])
    mddownloadpolicy = SelectionOption('sqlite', ('sqlite', 'xml'))
    #  ('instant', 'group:all', 'group:main', 'group:small', 'group:primary'))
    multilib_policy = SelectionOption(__main_multilib_policy_default__,
                                      ('best', 'all'))
                 # all == install any/all arches you can
                 # best == use the 'best  arch' for the system
                 
    requires_policy = SelectionOption('weak', ('strong', 'weak', 'info'),
                                      mapper={'information' : 'info',
                                              'informational' : 'info',
                                              'recommends' : 'weak',
                                              'suggests' : 'info'})
    bugtracker_url = Option('https://bugzilla.redhat.com/enter_bug.cgi?product=Fedora&version=rawhide&component=yum')

    color = SelectionOption('auto', ('auto', 'never', 'always'),
                            mapper={'on' : 'always', 'yes' : 'always',
                                    '1' : 'always', 'true' : 'always',
                                    'off' : 'never', 'no' : 'never',
                                    '0' : 'never', 'false' : 'never',
                                    'tty' : 'auto', 'if-tty' : 'auto'})
    color_list_installed_older = Option('bold')
    color_list_installed_newer = Option('bold,yellow')
    color_list_installed_reinstall = Option('normal')
    color_list_installed_extra = Option('bold,red')
    color_list_installed_running_kernel = Option('bold,underline')

    color_list_available_upgrade = Option('bold,blue')
    color_list_available_downgrade = Option('dim,cyan')
    color_list_available_reinstall = Option('bold,underline,green')
    color_list_available_install = Option('normal')
    color_list_available_running_kernel = Option('bold,underline')

    color_update_installed = Option('normal')
    color_update_local     = Option('bold')
    color_update_remote    = Option('normal')

    color_search_match = Option('bold')

    ui_repoid_vars = ListOption(['releasever', 'basearch'])
    
    sslcacert = Option()
    sslverify = BoolOption(True)
    sslclientcert = Option()
    sslclientkey = Option()
    ssl_check_cert_permissions = BoolOption(True)

    history_record = BoolOption(True)
    history_record_packages = ListOption(['yum', 'rpm'])

    rpmverbosity = Option('info')

    protected_packages = ListOption("yum, glob:/etc/yum/protected.d/*.conf",
                                    parse_default=True)
    protected_multilib = BoolOption(True)
    exit_on_lock = BoolOption(False)
    
    loadts_ignoremissing = BoolOption(False)
    loadts_ignorerpm = BoolOption(False)
    loadts_ignorenewrpm = BoolOption(False)
    autosavets = BoolOption(True)
    
    clean_requirements_on_remove = BoolOption(False)

    upgrade_requirements_on_install = BoolOption(False)

    history_list_view = SelectionOption('single-user-commands',
                                        ('single-user-commands', 'users',
                                         'commands'),
                                     mapper={'cmds'          : 'commands',
                                             'default' :'single-user-commands'})

    recheck_installed_requires = BoolOption(False)

    fssnap_automatic_pre  = BoolOption(False)
    fssnap_automatic_post = BoolOption(False)
    fssnap_automatic_keep = IntOption(1)
    fssnap_percentage = IntOption(100, range_min=1, range_max=100)
    fssnap_devices = ListOption("!*/swap !*/lv_swap "
                                "glob:/etc/yum/fssnap.d/*.conf",
                                parse_default=True)
    fssnap_abort_on_errors = SelectionOption('any', ('broken-setup', 'snapshot-failure', 'any', 'none'))

    depsolve_loop_limit = PositiveIntOption(100, names_of_0=["<forever>"])

    autocheck_running_kernel = BoolOption(True)

    check_config_file_age = BoolOption(True)

    usr_w_check = BoolOption(True)

    _reposlist = []

    def dump(self):
        """Return a string representing the values of all the
        configuration options.

        :return: a string representing the values of all the
           configuration options
        """
        output = '[main]\n'
        # we exclude all vars which start with _ or are in this list:
        excluded_vars = ('cfg', 'uid', 'yumvar', 'progress_obj', 'failure_obj',
                         'disable_excludes', 'config_file_age', 'config_file_path',
                         )
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

class RepoConf(BaseConfig):
    """Option definitions for repository INI file sections."""

    __cached_keys = set()
    def iterkeys(self):
        """Yield the names of all defined options in the instance."""

        ck = self.__cached_keys
        if not isinstance(self, RepoConf):
            ck = set()
        if not ck:
            ck.update(list(BaseConfig.iterkeys(self)))

        for name in self.__cached_keys:
            yield name

    name = Option()
    enabled = Inherit(YumConf.enabled)
    keepcache = Inherit(YumConf.keepcache)
    baseurl = UrlListOption()
    mirrorlist = UrlOption()
    metalink   = UrlOption()
    mediaid = Option()
    gpgkey = UrlListOption()
    gpgcakey = UrlListOption()
    exclude = ListOption() 
    includepkgs = ListOption() 

    proxy = Inherit(YumConf.proxy)
    proxy_username = Inherit(YumConf.proxy_username)
    proxy_password = Inherit(YumConf.proxy_password)
    retries = Inherit(YumConf.retries)
    failovermethod = Inherit(YumConf.failovermethod)
    username = Inherit(YumConf.username)
    password = Inherit(YumConf.password)

    # FIXME: rename gpgcheck to pkgs_gpgcheck
    gpgcheck = Inherit(YumConf.gpgcheck)
    repo_gpgcheck = Inherit(YumConf.repo_gpgcheck)
    keepalive = Inherit(YumConf.keepalive)
    enablegroups = Inherit(YumConf.enablegroups)

    minrate = Inherit(YumConf.minrate)
    bandwidth = Inherit(YumConf.bandwidth)
    throttle = Inherit(YumConf.throttle)
    timeout = Inherit(YumConf.timeout)
    ip_resolve = Inherit(YumConf.ip_resolve)
    #  This isn't inherited so that we can automatically disable file:// _only_
    # repos. if they haven't set an explicit deltarpm_percentage for the repo.
    deltarpm_percentage = IntOption(None, range_min=0, range_max=100)
    #  Rely on the above config. to do automatic disabling, and thus. no hack
    # needed here.
    deltarpm_metadata_percentage = Inherit(YumConf.deltarpm_metadata_percentage)
    ftp_disable_epsv = Inherit(YumConf.ftp_disable_epsv)

    http_caching = Inherit(YumConf.http_caching)
    metadata_expire = Inherit(YumConf.metadata_expire)
    metadata_expire_filter = Inherit(YumConf.metadata_expire_filter)
    mirrorlist_expire = Inherit(YumConf.mirrorlist_expire)
    # NOTE: metalink expire _must_ be the same as metadata_expire, due to the
    #       checksumming of the repomd.xml.
    mdpolicy = Inherit(YumConf.mdpolicy)
    mddownloadpolicy = Inherit(YumConf.mddownloadpolicy)
    cost = IntOption(1000)
    
    sslcacert = Inherit(YumConf.sslcacert)
    sslverify = Inherit(YumConf.sslverify)
    sslclientcert = Inherit(YumConf.sslclientcert)
    sslclientkey = Inherit(YumConf.sslclientkey)
    ssl_check_cert_permissions = Inherit(YumConf.ssl_check_cert_permissions)

    skip_if_unavailable = BoolOption(False)
    async = BoolOption(True)

    ui_repoid_vars = Inherit(YumConf.ui_repoid_vars)

    check_config_file_age = Inherit(YumConf.check_config_file_age)

    compare_providers_priority = IntOption(80, range_min=1, range_max=99)

    
class VersionGroupConf(BaseConfig):
    """Option definitions for version groups."""

    pkglist = ListOption()
    run_with_packages = BoolOption(False)

def _read_yumvars(yumvars, root):
    # Read the FS yumvars
    try:
        dir_fsvars = root + "/etc/yum/vars/"
        fsvars = os.listdir(dir_fsvars)
    except OSError:
        fsvars = []
    for fsvar in fsvars:
        if os.path.islink(dir_fsvars + fsvar):
            continue
        try:
            val = open(dir_fsvars + fsvar).readline()
            if val and val[-1] == '\n':
                val = val[:-1]
        except (OSError, IOError):
            continue
        yumvars[fsvar] = val

def readStartupConfig(configfile, root, releasever=None):
    """Parse Yum's main configuration file and return a
    :class:`StartupConf` instance.  This is required in order to
    access configuration settings required as Yum starts up.

    :param configfile: the path to yum.conf
    :param root: the base path to use for installation (typically '/')
    :return: A :class:`StartupConf` instance

    :raises: :class:`Errors.ConfigError` if a problem is detected with while parsing.
    """

    # ' xemacs syntax hack

    StartupConf.installroot.default = root
    startupconf = StartupConf()
    startupconf.config_file_path = configfile
    parser = ConfigParser()
    confpp_obj = ConfigPreProcessor(configfile)

    yumvars = _getEnvVar()
    _read_yumvars(yumvars, startupconf.installroot)
    confpp_obj._vars = yumvars
    startupconf.yumvars = yumvars

    try:
        parser.readfp(confpp_obj)
    except ParsingError, e:
        raise Errors.ConfigError("Parsing file failed: %s" % e)
    startupconf.populate(parser, 'main')

    # Check that plugin paths are all absolute
    for path in startupconf.pluginpath:
        if not path[0] == '/':
            raise Errors.ConfigError("All plugin search paths must be absolute")
    # Stuff this here to avoid later re-parsing
    startupconf._parser = parser

    # setup the release ver here
    if releasever is None:
        releasever = _getsysver(startupconf.installroot,
                                startupconf.distroverpkg)
    startupconf.releasever = releasever

    uuidfile = '%s/%s/uuid' % (startupconf.installroot, startupconf.persistdir)
    startupconf.uuid = get_uuid_obj(uuidfile)

    return startupconf

def readMainConfig(startupconf):
    """Parse Yum's main configuration file

    :param startupconf: :class:`StartupConf` instance as returned by readStartupConfig()
    :return: Populated :class:`YumConf` instance
    """
    
    # ' xemacs syntax hack

    # Set up substitution vars but make sure we always prefer FS yumvars
    yumvars = startupconf.yumvars
    yumvars.setdefault('basearch', startupconf.basearch)
    yumvars.setdefault('arch', startupconf.arch)
    yumvars.setdefault('releasever', startupconf.releasever)
    yumvars.setdefault('uuid', startupconf.uuid)
    
    # Read [main] section
    yumconf = YumConf()
    yumconf.populate(startupconf._parser, 'main')

    # Apply the installroot to directory options
    def _apply_installroot(yumconf, option):
        path = getattr(yumconf, option)
        ir_path = yumconf.installroot + path
        ir_path = ir_path.replace('//', '/') # os.path.normpath won't fix this and
                                             # it annoys me
        ir_path = varReplace(ir_path, yumvars)
        setattr(yumconf, option, ir_path)
    
    if StartupConf.installroot.default != yumconf.installroot:
        #  Note that this isn't perfect, in that if the initial installroot has
        # X=Y, and X doesn't exist in the new installroot ... then we'll still
        # have X afterwards (but if the new installroot has X=Z, that will be
        # the value after this).
        _read_yumvars(yumvars, yumconf.installroot)

    # These can use the above FS yumvars
    for option in ('cachedir', 'logfile', 'persistdir'):
        _apply_installroot(yumconf, option)

    # Add in some extra attributes which aren't actually configuration values 
    yumconf.yumvar = yumvars
    yumconf.uid = 0
    yumconf.cache = 0
    yumconf.progess_obj = None
    
    # items related to the originating config file
    yumconf.config_file_path = startupconf.config_file_path
    if os.path.exists(startupconf.config_file_path):
        yumconf.config_file_age = os.stat(startupconf.config_file_path)[8]
    else:
        yumconf.config_file_age = 0
    
    # propagate the debuglevel and errorlevel values:
    yumconf.debuglevel = startupconf.debuglevel
    yumconf.errorlevel = startupconf.errorlevel
    
    return yumconf

def readVersionGroupsConfig(configfile="/etc/yum/version-groups.conf"):
    """Parse the configuration file for version groups.
    
    :param configfile: the configuration file to read
    :return: a dictionary containing the parsed options
    """

    parser = ConfigParser()
    confpp_obj = ConfigPreProcessor(configfile)
    try:
        parser.readfp(confpp_obj)
    except ParsingError, e:
        raise Errors.ConfigError("Parsing file failed: %s" % e)
    ret = {}
    for section in parser.sections():
        ret[section] = VersionGroupConf()
        ret[section].populate(parser, section)
    return ret


def getOption(conf, section, name, option):
    """Convenience function to retrieve a parsed and converted value from a
    :class:`ConfigParser`.

    :param conf: ConfigParser instance or similar
    :param section: Section name
    :param name: :class:`Option` name
    :param option: :class:`Option` instance to use for conversion
    :return: The parsed value or default if value was not present
    :raises: :class:`ValueError` if the option could not be parsed
    """
    try: 
        val = conf.get(section, name)
    except (NoSectionError, NoOptionError):
        return option.default
    return option.parse(val)

def _getEnvVar():
    '''Return variable replacements from the environment variables YUM0 to YUM9

    The result is intended to be used with parser.varReplace()
    '''
    yumvar = {}
    for num in range(0, 10):
        env = 'YUM%d' % num
        val = os.environ.get(env, '')
        if val:
            yumvar[env.lower()] = val
    return yumvar

def _getsysver(installroot, distroverpkg):
    '''Calculate the release version for the system.

    @param installroot: The value of the installroot option.
    @param distroverpkg: The value of the distroverpkg option.
    @return: The release version as a string (eg. '4' for FC4)
    '''
    ts = rpmUtils.transaction.initReadOnlyTransaction(root=installroot)
    ts.pushVSFlags(~(rpm._RPMVSF_NOSIGNATURES|rpm._RPMVSF_NODIGESTS))
    try:
        for distroverpkg_prov in distroverpkg:
            idx = ts.dbMatch('provides', distroverpkg_prov)
            if idx.count():
                break
    except TypeError, e:
        # This is code for "cannot open rpmdb"
        # this is for pep 352 compliance on python 2.6 and above :(
        if sys.hexversion < 0x02050000:
            if hasattr(e,'message'):
                raise Errors.YumBaseError("Error: " + str(e.message))
            else:
                raise Errors.YumBaseError("Error: " + str(e))
        raise Errors.YumBaseError("Error: " + str(e))
    except rpm.error, e:
        # This is the "new" code for "cannot open rpmdb", 4.8.0 ish
        raise Errors.YumBaseError("Error: " + str(e))
    # we're going to take the first one - if there is more than one of these
    # then the user needs a beating
    if idx.count() == 0:
        releasever = '$releasever'
    else:
        try:
            hdr = idx.next()
        except StopIteration:
            raise Errors.YumBaseError("Error: rpmdb failed release provides. Try: rpm --rebuilddb")
        releasever = hdr['version']

        off = hdr[getattr(rpm, 'RPMTAG_PROVIDENAME')].index(distroverpkg_prov)
        flag = hdr[getattr(rpm, 'RPMTAG_PROVIDEFLAGS')][off]
        flag = rpmUtils.miscutils.flagToString(flag)
        ver  = hdr[getattr(rpm, 'RPMTAG_PROVIDEVERSION')][off]

        #  Note that if distroverpkg is the name of the package, then we can't
        # use the provide because rpm automatically adds a provide for the name
        # of the package. So just use the version, to be compatible.
        if hdr['name'] == distroverpkg_prov:
            flag = None
            ver  = None
        if flag == 'EQ' and ver:
            # override the package version
            releasever = ver

        del hdr
    del idx
    del ts
    return releasever

def _readRawRepoFile(repo):
    if not _use_iniparse:
        return None

    if not hasattr(repo, 'repofile') or not repo.repofile:
        return None

    try:
        ini = INIConfig(open(repo.repofile))
    except:
        return None

    # b/c repoids can have $values in them we need to map both ways to figure
    # out which one is which
    section_id = repo.id
    if repo.id not in ini._sections:
        for sect in ini._sections.keys():
            if varReplace(sect, repo.yumvar) == repo.id:
                section_id = sect
                break
        else:
            return None
    return ini, section_id

def writeRawRepoFile(repo,only=None):
    """Write changes in a repo object back to a .repo file.

    :param repo: the Repo Object to write back out
    :param only: list of attributes to work on. If *only* is None, all
       options will be written out   
    """
    if not _use_iniparse:
        return

    ini, section_id = _readRawRepoFile(repo)
    
    # Updated the ConfigParser with the changed values    
    cfgOptions = repo.cfg.options(repo.id)
    for name,value in repo.iteritems():
        if value is None: # Proxy
            continue

        if only is not None and name not in only:
            continue

        option = repo.optionobj(name)
        ovalue = option.tostring(value)
        #  If the value is the same, but just interpreted ... when we don't want
        # to keep the interpreted values.
        if (name in ini[section_id] and
            ovalue == varReplace(ini[section_id][name], repo.yumvar)):
            ovalue = ini[section_id][name]

        if name not in cfgOptions and option.default == value:
            continue

        ini[section_id][name] = ovalue
    fp =file(repo.repofile,"w")               
    fp.write(str(ini))
    fp.close()

# Copied from yum-config-manager ... how we alter yu.conf ... used in "yum fs"
def _writeRawConfigFile(filename, section_id, yumvar,
                        cfgoptions, items, optionobj,
                        only=None):
    """
    From writeRawRepoFile, but so we can alter [main] too.
    """
    ini = INIConfig(open(filename))

    osection_id = section_id
    # b/c repoids can have $values in them we need to map both ways to figure
    # out which one is which
    if section_id not in ini._sections:
        for sect in ini._sections.keys():
            if varReplace(sect, yumvar) == section_id:
                section_id = sect

    # Updated the ConfigParser with the changed values
    cfgOptions = cfgoptions(osection_id)
    for name,value in items():
        if value is None: # Proxy
            continue

        if only is not None and name not in only:
            continue

        option = optionobj(name)
        ovalue = option.tostring(value)
        #  If the value is the same, but just interpreted ... when we don't want
        # to keep the interpreted values.
        if (name in ini[section_id] and
            ovalue == varReplace(ini[section_id][name], yumvar)):
            ovalue = ini[section_id][name]

        if name not in cfgOptions and option.default == value:
            continue

        ini[section_id][name] = ovalue
    fp =file(filename, "w")
    fp.write(str(ini))
    fp.close()

#def main():
#    mainconf = readMainConfig(readStartupConfig('/etc/yum/yum.conf', '/'))
#    print mainconf.cachedir
#
#if __name__ == '__main__':
#    main()
