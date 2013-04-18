#!/usr/bin/python -tt
import os
import sys
import gzip
from socket import gethostname

import yum
import yum.Errors
from yum.config import BaseConfig, Option, IntOption, ListOption, BoolOption
from yum.parser import ConfigPreProcessor
from ConfigParser import ConfigParser, ParsingError
from yum.constants import *
from email.mime.text import MIMEText
from yum.i18n import to_str, to_utf8, to_unicode, utf8_width, utf8_width_fill, utf8_text_fill
from yum import  _, P_
import smtplib
from random import random
from time import sleep

# FIXME: is it really sane to use this from here?
sys.path.append('/usr/share/yum-cli')
import callback

default_config_file = '/etc/yum/yum-cron.conf'

class UpdateEmitter(object):
    """Abstract class for implementing different types of emitters.
       Most methods will add certain messages the output list.  Then,
       the sendMessage method can be overridden in a subclass to
       combine these messages and transmit them as required.
    """

    def __init__(self, opts):
        self.opts  = opts
        self.output = []

    def updatesAvailable(self, tsInfo):
        """Appends a message to the output list stating that there are
        updates available.

        :param tsInfo: A :class:`yum.transactioninfo.TransactionData`
           instance that contains information about the transaction.
        """
        self.output.append('The following updates are available on %s:' % self.opts.system_name)
        self.output.append(self._formatTransaction(tsInfo))

    def updatesDownloading(self, tsInfo):
        """Append a message to the output list stating that
        downloading updates has started.

        :param tsInfo: A :class:`yum.transactioninfo.TransactionData`
           instance that contains information about the transaction.
        """
        self.output.append('The following updates will be downloaded on %s:' % self.opts.system_name)
        self.output.append(self._formatTransaction(tsInfo))

    def updatesDownloaded(self):
        """Append a message to the output list stating that updates
        have been downloaded successfully.
        """
        self.output.append("Updates downloaded successfully.")

    def updatesInstalling(self, tsInfo):
        """Append a message to the output list stating that
        installing updates has started.

        :param tsInfo: A :class:`yum.transactioninfo.TransactionData`
           instance that contains information about the transaction.
        """
        self.output.append('The following updates will be applied on %s:' % self.opts.system_name)
        self.output.append(self._formatTransaction(tsInfo))

    def updatesInstalled(self):
        """Append a message to the output list stating that updates
        have been installed successfully.
        """
        self.output.append('The updates were successfully applied')

    def setupFailed(self, errmsg):
        """Append a message to the output list stating that setup
        failed, and then call sendMessages to emit the output.

        :param errmsgs: a string that contains the error message
        """
        self.output.append("Plugins failed to initialize with the following error message: \n%s" 
                           % errmsg)
        self.sendMessages()

    def lockFailed(self, errmsg):
        """Append a message to the output list stating that the
        program failed to acquire the yum lock, then call sendMessages
        to emit the output.

        :param errmsg: a string that contains the error message
        """
        self.output.append("Failed to acquire the yum lock with the following error message: \n%s"
                           % errmsg)
        self.sendMessages()

    def checkFailed(self, errmsg):
        """Append a message to the output stating that checking for
        updates failed, then call sendMessages to emit the output.

        :param errmsgs: a string that contains the error message
        """
        self.output.append("Failed to check for updates with the following error message: \n%s" 
                           % errmsg)
        self.sendMessages()

    def groupError(self, errmsg):
        """Append a message to the output list stating that an error
        was encountered while checking for group updates.

        :param errmsgs: a string that contains the error message
        """
        self.output.append("Error checking for group updates: \n%s" 
                           % errmsg)

    def groupFailed(self, errmsg):
        """Append a message to the output list stating that checking
        for group updates failed, then call sendMessages to emit the output.

        :param errmsgs: a string that contains the error message
        """
        self.output.append("Failed to check for updates with the following error message: \n%s" 
                           % errmsg)
        self.sendMessages()

    def downloadFailed(self, errmsg):
        """Append a message to the output list stating that
        downloading updates failed, then call sendMessages to emit the output.

        :param errmsgs: a string that contains the error message
        """
        self.output.append("Updates failed to download with the following error message: \n%s"
                      % errmsg)
        self.sendMessages()
        
    def updatesFailed(self, errmsg):
        """Append a message to the output list stating that installing
        updates failed, then call sendMessages to emit the output.

        :param errmsgs: a string that contains the error message
        """
        self.output.append("Updates failed to install with the following error message: \n%s"
                      % errmsg)
        self.sendMessages()

    def sendMessages(self):
        """Send the messages that have been stored.  This should be
        overridden by inheriting classes to emit the messages
        according to their individual methods.
        """
        pass

    def _format_number(self, number, SI=0, space=' '):
        """Return a human-readable metric-like string representation
        of a number.

        :param number: the number to be converted to a human-readable form
        :param SI: If is 0, this function will use the convention
           that 1 kilobyte = 1024 bytes, otherwise, the convention
           that 1 kilobyte = 1000 bytes will be used
        :param space: string that will be placed between the number
           and the SI prefix
        :return: a human-readable metric-like string representation of
           *number*
        """
        symbols = [ ' ', # (none)
                    'k', # kilo
                    'M', # mega
                    'G', # giga
                    'T', # tera
                    'P', # peta
                    'E', # exa
                    'Z', # zetta
                    'Y'] # yotta
    
        if SI: step = 1000.0
        else: step = 1024.0
    
        thresh = 999
        depth = 0
        max_depth = len(symbols) - 1
    
        # we want numbers between 0 and thresh, but don't exceed the length
        # of our list.  In that event, the formatting will be screwed up,
        # but it'll still show the right number.
        while number > thresh and depth < max_depth:
            depth  = depth + 1
            number = number / step
    
        if type(number) == type(1) or type(number) == type(1L):
            format = '%i%s%s'
        elif number < 9.95:
            # must use 9.95 for proper sizing.  For example, 9.99 will be
            # rounded to 10.0 with the .1f format string (which is too long)
            format = '%.1f%s%s'
        else:
            format = '%.0f%s%s'
    
        return(format % (float(number or 0), space, symbols[depth]))

    def _fmtColumns(self, columns, msg=u'', end=u'', text_width=utf8_width):
        """Return a row of data formatted into a string for output.
        Items can overflow their columns. 

        :param columns: a list of tuples containing the data to
           output.  Each tuple contains first the item to be output,
           then the amount of space allocated for the column, and then
           optionally a type of highlighting for the item
        :param msg: a string to begin the line of output with
        :param end: a string to end the line of output with
        :param text_width: a function to find the width of the items
           in the columns.  This defaults to utf8 but can be changed
           to len() if you know it'll be fine
        :return: a row of data formatted into a string for output
        """
        total_width = len(msg)
        data = []
        for col_data in columns[:-1]:
            (val, width) = col_data

            if not width: # Don't count this column, invisible text
                msg += u"%s"
                data.append(val)
                continue

            (align, width) = self._fmt_column_align_width(width)
            val_width = text_width(val)
            if val_width <= width:
                #  Don't use utf8_width_fill() because it sucks performance
                # wise for 1,000s of rows. Also allows us to use len(), when
                # we can.
                msg += u"%s%s "
                if (align == u'-'):
                    data.extend([val, " " * (width - val_width)])
                else:
                    data.extend([" " * (width - val_width), val])
            else:
                msg += u"%s\n" + " " * (total_width + width + 1)
                data.append(val)
            total_width += width
            total_width += 1
        (val, width) = columns[-1]
        (align, width) = self._fmt_column_align_width(width)
        val = utf8_width_fill(val, width, left=(align == u'-'))
        msg += u"%%s%s" % end
        data.append(val)
        return msg % tuple(data)

    def _calcColumns(self, data, total_width, columns=None, remainder_column=0, indent=''):
        """Dynamically calculate the widths of the columns that the
        fields in data should be placed into for output.
        
        :param data: a list of dictionaries that represent the data to
           be output.  Each dictionary in the list corresponds to annn
           column of output. The keys of the dictionary are the
           lengths of the items to be output, and the value associated
           with a key is the number of items of that length.
        :param total_width: the total width of the output.
        :param columns: a list containing the minimum amount of space
           that must be allocated for each row. This can be used to
           ensure that there is space available in a column if, for
           example, the actual lengths of the items being output
           cannot be given in *data*
        :param remainder_column: number of the column to receive a few
           extra spaces that may remain after other allocation has
           taken place
        :param indent: string that will be prefixed to a line of
           output to create e.g. an indent
        :return: a list of the widths of the columns that the fields
           in data should be placed into for output
        """
        if total_width is None:
            total_width = self.term.columns

        cols = len(data)
        # Convert the data to ascending list of tuples, (field_length, pkgs)
        pdata = data
        data  = [None] * cols # Don't modify the passed in data
        for d in range(0, cols):
            data[d] = sorted(pdata[d].items())

        #  We start allocating 1 char to everything but the last column, and a
        # space between each (again, except for the last column). Because
        # at worst we are better with:
        # |one two three|
        # | four        |
        # ...than:
        # |one two three|
        # |            f|
        # |our          |
        # ...the later being what we get if we pre-allocate the last column, and
        # thus. the space, due to "three" overflowing it's column by 2 chars.
        if columns is None:
            columns = [1] * (cols - 1)
            columns.append(0)

        total_width -= (sum(columns) + (cols - 1) +
                        utf8_width(indent))
        if not columns[-1]:
            total_width += 1
        while total_width > 0:
            # Find which field all the spaces left will help best
            helps = 0
            val   = 0
            for d in xrange(0, cols):
                thelps = self._calc_columns_spaces_helps(columns[d], data[d],
                                                         total_width)
                if not thelps:
                    continue
                #  We prefer to overflow: the last column, and then earlier
                # columns. This is so that in the best case (just overflow the
                # last) ... grep still "works", and then we make it prettier.
                if helps and (d == (cols - 1)) and (thelps / 2) < helps:
                    continue
                if thelps < helps:
                    continue
                helps = thelps
                val   = d

            #  If we found a column to expand, move up to the next level with
            # that column and start again with any remaining space.
            if helps:
                diff = data[val].pop(0)[0] - columns[val]
                if not columns[val] and (val == (cols - 1)):
                    #  If we are going from 0 => N on the last column, take 1
                    # for the space before the column.
                    total_width  -= 1
                columns[val] += diff
                total_width  -= diff
                continue

            overflowed_columns = 0
            for d in xrange(0, cols):
                if not data[d]:
                    continue
                overflowed_columns += 1
            if overflowed_columns:
                #  Split the remaining spaces among each overflowed column
                # equally
                norm = total_width / overflowed_columns
                for d in xrange(0, cols):
                    if not data[d]:
                        continue
                    columns[d] += norm
                    total_width -= norm

            #  Split the remaining spaces among each column equally, except the
            # last one. And put the rest into the remainder column
            cols -= 1
            norm = total_width / cols
            for d in xrange(0, cols):
                columns[d] += norm
            columns[remainder_column] += total_width - (cols * norm)
            total_width = 0

        return columns

    @staticmethod
    def _fmt_column_align_width(width):
        if width < 0:
            return (u"-", -width)
        return (u"", width)

    @staticmethod
    def _calc_columns_spaces_helps(current, data_tups, left):
        """ Spaces left on the current field will help how many pkgs? """
        ret = 0
        for tup in data_tups:
            if left < (tup[0] - current):
                break
            ret += tup[1]
        return ret

    def _formatTransaction(self, tsInfo):
        """Return a string containing a human-readable formatted
        summary of the transaction.
        
        :param tsInfo: :class:`yum.transactioninfo.TransactionData`
           instance that contains information about the transaction
        :return: a string that contains a formatted summary of the
           transaction
           """
        # Sort the packages in the transaction into different lists,
        # e.g. installed, updated etc
        tsInfo.makelists(True, True)

        # For each package list, pkglist_lines will contain a tuple
        # that contains the name of the list, and a list of tuples
        # with information about each package in the list
        pkglist_lines = []
        data  = {'n' : {}, 'v' : {}, 'r' : {}}
        a_wid = 0 # Arch can't get "that big" ... so always use the max.


        def _add_line(lines, data, a_wid, po, obsoletes=[]):
            # Create a tuple of strings that contain the name, arch,
            # version, repository, size, and obsoletes of the package
            # given in po.  Then, append this tuple to lines.  The
            # strings are formatted so that the tuple can be easily
            # joined together for output.

            
            (n,a,e,v,r) = po.pkgtup
            
            # Retrieve the version, repo id, and size of the package
            # in human-readable form
            evr = po.printVer()
            repoid = po.ui_from_repo
            size = self._format_number(float(po.size))

            if a is None: # gpgkeys are weird
                a = 'noarch'

            lines.append((n, a, evr, repoid, size, obsoletes))
            #  Create a dict of field_length => number of packages, for
            # each field.
            for (d, v) in (("n",len(n)), ("v",len(evr)), ("r",len(repoid))):
                data[d].setdefault(v, 0)
                data[d][v] += 1
            a_wid = max(a_wid, len(a))

            return a_wid

        

        # Iterate through the different groups of packages
        for (action, pkglist) in [(_('Installing'), tsInfo.installed),
                            (_('Updating'), tsInfo.updated),
                            (_('Removing'), tsInfo.removed),
                            (_('Reinstalling'), tsInfo.reinstalled),
                            (_('Downgrading'), tsInfo.downgraded),
                            (_('Installing for dependencies'), tsInfo.depinstalled),
                            (_('Updating for dependencies'), tsInfo.depupdated),
                            (_('Removing for dependencies'), tsInfo.depremoved)]:
            # Create a list to hold the tuples of strings for each package
            lines = []

            # Append the tuple for each package to lines, and update a_wid
            for txmbr in pkglist:
                a_wid = _add_line(lines, data, a_wid, txmbr.po, txmbr.obsoletes)

            # Append the lines instance for this package list to pkglist_lines
            pkglist_lines.append((action, lines))

        # # Iterate through other package lists
        # for (action, pkglist) in [(_('Skipped (dependency problems)'),
        #                            self.skipped_packages),
        #                           (_('Not installed'), self._not_found_i.values()),
        #                           (_('Not available'), self._not_found_a.values())]:
        #     lines = []
        #     for po in pkglist:
        #         a_wid = _add_line(lines, data, a_wid, po)

        #     pkglist_lines.append((action, lines))

        if not data['n']:
            return u''
        else:
            # Change data to a list with the correct number of
            # columns, in the correct order
            data    = [data['n'],    {}, data['v'], data['r'], {}]

            
             
            # Calculate the space needed for each column
            columns = [1,         a_wid,         1,         1,  5]

            columns = self._calcColumns(data, self.opts.output_width,
                                        columns, remainder_column = 2, indent="  ")

            (n_wid, a_wid, v_wid, r_wid, s_wid) = columns
            assert s_wid == 5

            # out will contain the output as a list of strings, that
            # can be later joined together
            out = [u"""
%s
%s
%s
""" % ('=' * self.opts.output_width,
       self._fmtColumns(((_('Package'), -n_wid), (_('Arch'), -a_wid),
                        (_('Version'), -v_wid), (_('Repository'), -r_wid),
                        (_('Size'), s_wid)), u" "),
       '=' * self.opts.output_width)]

        # Add output for each package list in pkglist_lines
        for (action, lines) in pkglist_lines:
            #If the package list is empty, skip it
            if not lines:
                continue

            # Add the name of the package list
            totalmsg = u"%s:\n" % action
            # Add a line of output about an individual package
            for (n, a, evr, repoid, size, obsoletes) in lines:
                columns = ((n,   -n_wid), (a,      -a_wid),
                           (evr, -v_wid), (repoid, -r_wid), (size, s_wid))
                msg = self._fmtColumns(columns, u" ", u"\n")
                for obspo in sorted(obsoletes):
                    appended = _('     replacing  %s.%s %s\n')
                    appended %= (obspo.name,
                                 obspo.arch, obspo.printVer())
                    msg = msg+appended
                totalmsg = totalmsg + msg

            # Append the line about the individual package to out
            out.append(totalmsg)

        # Add a summary of the transaction
        out.append(_("""
Transaction Summary
%s
""") % ('=' * self.opts.output_width))
        summary_data =  (
            (_('Install'), len(tsInfo.installed),
             len(tsInfo.depinstalled)),
            (_('Upgrade'), len(tsInfo.updated),
             len(tsInfo.depupdated)),
            (_('Remove'), len(tsInfo.removed),
             len(tsInfo.depremoved)),
            (_('Reinstall'), len(tsInfo.reinstalled), 0),
            (_('Downgrade'), len(tsInfo.downgraded), 0),
            # (_('Skipped (dependency problems)'), len(self.skipped_packages), 0),
            # (_('Not installed'), len(self._not_found_i.values()), 0),
            # (_('Not available'), len(self._not_found_a.values()), 0),
        )
        max_msg_action   = 0
        max_msg_count    = 0
        max_msg_pkgs     = 0
        max_msg_depcount = 0
        for action, count, depcount in summary_data:
            if not count and not depcount:
                continue

            msg_pkgs = P_('Package', 'Packages', count)
            len_msg_action   = utf8_width(action)
            len_msg_count    = utf8_width(str(count))
            len_msg_pkgs     = utf8_width(msg_pkgs)

            if depcount:
                len_msg_depcount = utf8_width(str(depcount))
            else:
                len_msg_depcount = 0

            max_msg_action   = max(len_msg_action,   max_msg_action)
            max_msg_count    = max(len_msg_count,    max_msg_count)
            max_msg_pkgs     = max(len_msg_pkgs,     max_msg_pkgs)
            max_msg_depcount = max(len_msg_depcount, max_msg_depcount)

        for action, count, depcount in summary_data:
            msg_pkgs = P_('Package', 'Packages', count)
            if depcount:
                msg_deppkgs = P_('Dependent package', 'Dependent packages',
                                 depcount)
                if count:
                    msg = '%s  %*d %s (+%*d %s)\n'
                    out.append(msg % (utf8_width_fill(action, max_msg_action),
                                      max_msg_count, count,
                                      utf8_width_fill(msg_pkgs, max_msg_pkgs),
                                      max_msg_depcount, depcount, msg_deppkgs))
                else:
                    msg = '%s  %*s %s ( %*d %s)\n'
                    out.append(msg % (utf8_width_fill(action, max_msg_action),
                                      max_msg_count, '',
                                      utf8_width_fill('', max_msg_pkgs),
                                      max_msg_depcount, depcount, msg_deppkgs))
            elif count:
                msg = '%s  %*d %s\n'
                out.append(msg % (utf8_width_fill(action, max_msg_action),
                                  max_msg_count, count, msg_pkgs))

        return ''.join(out)


class EmailEmitter(UpdateEmitter):
    """Emitter class to send messages via email."""

    def __init__(self, opts):
        super(EmailEmitter, self).__init__(opts)        
        self.subject = ""

    def updatesAvailable(self, tsInfo):
        """Appends a message to the output list stating that there are
        updates available, and set an appropriate subject line.

        :param tsInfo: A :class:`yum.transactioninfo.TransactionData`
           instance that contains information about the transaction.
        """
        super(EmailEmitter, self).updatesAvailable(tsInfo)
        self.subject = "Yum: Updates Available on %s" % self.opts.system_name

    def updatesDownloaded(self):
        """Append a message to the output list stating that updates
        have been downloaded successfully, and set an appropriate
        subject line.
        """
        self.subject = "Yum: Updates downloaded on %s" % self.opts.system_name
        super(EmailEmitter, self).updatesDownloaded()

    def updatesInstalled(self):
        """Append a message to the output list stating that updates
        have been installed successfully, and set an appropriate
        subject line.
        """
        self.subject = "Yum: Updates installed on %s" % self.opts.system_name
        super(EmailEmitter, self).updatesInstalled()

    def setupFailed(self, errmsg):
        """Append a message to the output list stating that setup
        failed, and then call sendMessages to emit the output, and set
        an appropriate subject line.

        :param errmsgs: a string that contains the error message
        """
        self.subject = "Yum: Failed to perform setup on %s" % self.opts.system_name
        super(EmailEmitter, self).setupFailed(errmsg)

    def lockFailed(self, errmsg):
        """Append a message to the output list stating that the
        program failed to acquire the yum lock, then call sendMessages
        to emit the output, and set an appropriate subject line.

        :param errmsg: a string that contains the error message
        """
        self.subject = "Yum: Failed to  acquire the yum lock on %s" % self.opts.system_name
        super(EmailEmitter, self).lockFailed(errmsg)

    def checkFailed(self, errmsg):
        """Append a message to the output stating that checking for
        updates failed, then call sendMessages to emit the output, and
        set an appropriate subject line.

        :param errmsgs: a string that contains the error message
        """
        self.subject = "Yum: Failed to check for updates on %s" % self.opts.system_name
        super(EmailEmitter, self).checkFailed(errmsg)

    def downloadFailed(self, errmsg):
        """Append a message to the output list stating that checking
        for group updates failed, then call sendMessages to emit the
        output, and add an appropriate subject line.

        :param errmsgs: a string that contains the error message
        """
        self.subject = "Yum: Failed to download updates on %s" % self.opts.system_name
        super(EmailEmitter, self).downloadFailed(errmsg)

    def updatesFailed(self, errmsg):
        """Append a message to the output list stating that installing
        updates failed, then call sendMessages to emit the output, and
        add an appropriate subject line.

        :param errmsgs: a string that contains the error message
        """
        self.subject = "Yum: Failed to install updates on %s" % self.opts.system_name
        super(EmailEmitter, self).updatesFailed(errmsg)

    def sendMessages(self):
        """Combine the stored messages that have been stored into a
        single email message, and send this message.
        """
        # Build up the email to be sent
        msg = MIMEText(''.join(self.output))
        msg['Subject'] = self.subject
        msg['From'] = self.opts.email_from
        msg['To'] = ",".join(self.opts.email_to)

        # Send the email
        s = smtplib.SMTP()
        s.connect(self.opts.email_host)
        s.sendmail(self.opts.email_from, self.opts.email_to, msg.as_string())
        s.close()


class StdIOEmitter(UpdateEmitter):
    """Emitter class to send messages to syslog."""

    def __init__(self, opts):
        super(StdIOEmitter, self).__init__(opts)
        
    def sendMessages(self) :
        """Combine the stored messages that have been stored into a
        single email message, and send this message to standard output.
        """
        print "".join(self.output)


class YumCronConfig(BaseConfig):
    """Class to parse configuration information from the config file, and
    to store this information.
    """
    system_name = Option(gethostname())
    output_width = IntOption(80)
    random_sleep = IntOption(0)
    emit_via = ListOption(['email','stdio'])
    email_to = ListOption(["root"])
    email_from = Option("root")
    email_host = Option("localhost")
    email_port = IntOption(25)
    update_messages = BoolOption(False)
    apply_updates = BoolOption(False)
    download_updates = BoolOption(False)
    yum_config_file = Option("/etc/yum.conf")
    group_list = ListOption([])
    group_package_types = ListOption(['mandatory', 'default'])
    skip_broken = BoolOption()


class YumCronBase(yum.YumBase):
    """Main class to check for and apply the updates."""

    def __init__(self, config_file_name = None):
        """Create a YumCronBase object, and perform initial setup.

        :param config_file_name: a String specifying the name of the
           config file to use.
        """
        yum.YumBase.__init__(self)

        # Read the config file
        self.readConfigFile(config_file_name)


        # Create the emitters, and add them to the list
        self.emitters = []
        if 'email' in self.opts.emit_via:
            self.emitters.append(EmailEmitter(self.opts))
        if 'stdio' in self.opts.emit_via:
            self.emitters.append(StdIOEmitter(self.opts))

        self.updateInfo = []
        self.updateInfoTime = None

    def readConfigFile(self, config_file_name = None):
        """Reads the given config file, or if none is given, the
        default config file.

        :param config_file_name: a String specifying the name of the
           config file to read.
        """
        # Create ConfigParser and UDConfig Objects
        confparser = ConfigParser()
        self.opts = YumCronConfig()

        #If no config file name is given, fall back to the default
        if config_file_name == None:
            config_file_name = default_config_file
            
        # Attempt to read the config file.  confparser.read will return a
        # list of the files that were read successfully, so check that it
        # contains config_file
        if config_file_name not in confparser.read(config_file_name):
            print >> sys.stderr, "Error reading config file"
            sys.exit(1)

        # Populate the values into  the opts object
        self.opts.populate(confparser, 'commands')
        self.opts.populate(confparser, 'emitters')
        self.opts.populate(confparser, 'email')
        self.opts.populate(confparser, 'groups')

        #If the system name is not given, set it by getting the hostname
        if self.opts.system_name == 'None' :
            self.opts.system_name = gethostname()

        if 'None' in self.opts.group_list:
            self.opts.group_list = []


    def randomSleep(self, duration):
        """Sleep for a random amount of time up to *duration*.
        
        :param duration: the maximum amount of time to sleep, in
           minutes.  The actual time slept will be between 0 and
           *duration* minutes
           """
        if duration > 0:
            sleep(random() * 60 * duration)

    def doSetup(self):
        """Perform set up, including setting up directories and
        parsing options.

        :return: boolean that indicates whether setup has completed
           successfully
        """
        try :
            # Set the configuration file
            self.preconf.fn = self.opts.yum_config_file

            # if we are not root do the special subdir thing
            if os.geteuid() != 0:
                self.setCacheDir()

            # Turn off the plugins line
            self.preconf.debuglevel = 0

            # Create the configuration
            self.conf

        except Exception, e:
            # If there are any exceptions, send a message about them,
            # and return False
            self.emitSetupFailed('%s' % e)
            sys.exit(1)

        # override yum options
        if self.opts.skip_broken is not None:
            self.conf.skip_broken = self.opts.skip_broken

    def acquireLock(self):
        """ Wrapper method around doLock to emit errors correctly."""

        try:
            self.doLock()
        except yum.Errors.LockError, e:
            self.emitLockFailed("%s" % e)
            sys.exit(1)

    def populateUpdateMetadata(self):
        """Populate the metadata for the packages in the update."""
        self.upinfo

    def refreshUpdates(self):
        """Check whether updates are available.

        :return: Boolean indicating whether any updates are
           available
        """
        try:
            updatesTuples = self.up.getUpdatesTuples()
            # If there are no updates, return False
            if not updatesTuples:
                return False

            # figure out the updates
            for (new, old) in updatesTuples:
                updates_available = True
                updating = self.getPackageObject(new)
                updated = self.rpmdb.searchPkgTuple(old)[0]
            
                self.tsInfo.addUpdate(updating, updated)

            # and the obsoletes
            if self.conf.obsoletes:
                for (obs, inst) in self.up.getObsoletesTuples():
                    obsoleting = self.getPackageObject(obs)
                    installed = self.rpmdb.searchPkgTuple(inst)[0]
                
                    self.tsInfo.addObsoleting(obsoleting, installed)
                    self.tsInfo.addObsoleted(installed, obsoleting)

        except Exception, e:
            self.emitCheckFailed("%s" %(e,))
            sys.exit(1)

        else:
            return True

    def refreshGroupUpdates(self):
        """Check for group updates, and add them to the
        transaction.

        :return: Boolean indicating whether there are any updates to
           the group available
        """
        if self.conf.group_command == 'objects':
            return False

        update_available = False
        try:
            for group_string in self.opts.group_list:
                group_matched = False
                for group in self.comps.return_groups(group_string):
                    group_matched = True
                    try:
                        txmbrs = self.selectGroup(group.groupid,
                                                  self.opts.group_package_types,
                                                  upgrade=True)
                        
                        # If updates are available from a previous
                        # group, or there are updates are available
                        # from this group, set update_available to True
                        update_available |= (txmbrs != [])
                        
                    except yum.Errors.GroupsError:
                        self.emitGroupError('Warning: Group %s does not exist.' % group_string)
                        continue

                if not group_matched:
                    self.emitGroupError('Warning: Group %s does not exist.' % group_string)
                    continue

        except Exception, e:
            self.emitGroupFailed("%s" % e)
            return False

        else:
            return update_available

    def findDeps(self):
        """Build the transaction to resolve the dependencies for the update."""

        try:
            (res, resmsg) = self.buildTransaction()
        except yum.Errors.RepoError, e:
            self.emitCheckFailed("%s" %(e,))
            sys.exit()
        if res != 2:
            self.emitCheckFailed("Failed to build transaction: %s" %(str.join("\n", resmsg),))
            sys.exit(1)

    def downloadUpdates(self, emit):
        """Download the update.

        :param emit: Boolean indicating whether to emit messages
           about the download
        """
        # Emit a message that that updates will be downloaded
        if emit :
            self.emitDownloading()
        dlpkgs = map(lambda x: x.po, filter(lambda txmbr:
                                            txmbr.ts_state in ("i", "u"),
                                            self.tsInfo.getMembers()))
        try:
            # Download the updates
            self.conf.download_only = not self.opts.apply_updates
            self.downloadPkgs(dlpkgs)
        except Exception, e:
            self.emitDownloadFailed("%s" % e)
            sys.exit(1)
        except SystemExit, e:
            if e.code == 0:
                # Emit a message that the packages have been downloaded
                self.emitDownloaded()
                self.emitMessages()
            raise

    def installUpdates(self, emit):
        """Apply the available updates.
        
        :param emit: Boolean indicating whether to emit messages about
           the installation
        """
        # Emit a message  that 
        if emit :
            self.emitInstalling()

        dlpkgs = map(lambda x: x.po, filter(lambda txmbr:
                                            txmbr.ts_state in ("i", "u"),
                                            self.tsInfo.getMembers()))

        for po in dlpkgs:
            result, err = self.sigCheckPkg(po)
            if result == 0:
                continue
            elif result == 1:
                try:
                    self.getKeyForPackage(po)
                except yum.Errors.YumBaseError, errmsg:
                    self.emitUpdateFailed([str(errmsg)])
                    return False

        del self.ts
        self.initActionTs() # make a new, blank ts to populate
        self.populateTs(keepold=0)
        self.ts.check() #required for ordering
        self.ts.order() # order
        cb = callback.RPMInstallCallback(output = 0)
        cb.filelog = True
            
        cb.tsInfo = self.tsInfo
        try:
            self.runTransaction(cb=cb)
        except yum.Errors.YumBaseError, err:
            
            self.emitUpdateFailed([str(err)])
            sys.exit(1)

        self.emitInstalled()
        self.emitMessages()

    def updatesCheck(self):
        """Check to see whether updates are available for any
        installed packages. If updates are available, install them,
        download them, or just emit a message, depending on what
        options are selected in the configuration file.
        """
        # Sleep a random time
        self.randomSleep(self.opts.random_sleep)

        # Perform the initial setup
        self.doSetup()

        # Acquire the yum lock
        self.acquireLock()

        # Update the metadata
        self.populateUpdateMetadata()

        # Exit if we don't need to send messages, or there are no
        # updates
        if not (self.opts.update_messages and (self.refreshUpdates()
                                             or self.refreshGroupUpdates())):
                sys.exit(0)

        # Build the transaction to find the additional dependencies
        self.findDeps()

        # download if set up to do so, else tell about the updates and exit
        if not self.opts.download_updates:
            self.emitAvailable()
            self.emitMessages()
            self.releaseLocks()
            sys.exit(0)

        self.downloadUpdates(not self.opts.apply_updates)
        
        # now apply if we're set up to do so; else just tell that things are
        # available
        if not self.opts.apply_updates:
            self.releaseLocks()
            sys.exit(0)

        self.installUpdates(True)

        self.releaseLocks()
        sys.exit(0)

    def releaseLocks(self):
        """Close the rpm database, and release the yum lock."""
        self.closeRpmDB()
        self.doUnlock()

    def emitAvailable(self):
        """Emit a notice stating whether updates are available."""
        map(lambda x: x.updatesAvailable(self.tsInfo), self.emitters)

    def emitDownloading(self):
        """Emit a notice stating that updates are downloading."""
        map(lambda x: x.updatesDownloading(self.tsInfo), self.emitters)

    def emitDownloaded(self):
        """Emit a notice stating that updates have downloaded."""
        map(lambda x: x.updatesDownloaded(), self.emitters)

    def emitInstalling(self):
        """Emit a notice stating that automatic updates are about to
        be applied.
        """
        map(lambda x: x.updatesInstalling(self.tsInfo), self.emitters)

    def emitInstalled(self):
        """Emit a notice stating that automatic updates have been applied."""
        map(lambda x: x.updatesInstalled(), self.emitters)

    def emitSetupFailed(self, error):
        """Emit a notice stating that checking for updates failed."""
        map(lambda x: x.setupFailed(error), self.emitters)

    def emitLockFailed(self, errmsg):
        """Emit a notice that we failed to acquire the yum lock."""
        map(lambda x: x.lockFailed(errmsg), self.emitters)

    def emitCheckFailed(self, error):
        """Emit a notice stating that checking for updates failed."""
        map(lambda x: x.checkFailed(error), self.emitters)

    def emitGroupError(self, error):
        """Emit a notice stating that there was an error checking for
        group updates.
        """
        map(lambda x: x.groupError(error), self.emitters)

    def emitGroupFailed(self, error):
        """Emit a notice stating that checking for group updates failed."""
        map(lambda x: x.groupFailed(error), self.emitters)

    def emitDownloadFailed(self, error):
        """Emit a notice stating that downloading the updates failed."""
        map(lambda x: x.downloadFailed(error), self.emitters)

    def emitUpdateFailed(self, errmsgs):
        """Emit a notice stating that automatic updates failed."""
        map(lambda x: x.updatesFailed(errmsgs), self.emitters)

    def emitMessages(self):
        """Emit the messages from the emitters."""
        map(lambda x: x.sendMessages(), self.emitters)


def main():
    """Configure and run the update check."""
    # If a file name was passed in, use it as the config file name.
    base = None
    if len(sys.argv) > 1:
        base = YumCronBase(sys.argv[1])
    else:
        base = YumCronBase()

    #Run the update check
    base.updatesCheck()

if __name__ == "__main__":
    main()
