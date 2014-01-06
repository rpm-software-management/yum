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
import yum.updateinfo
import smtplib
from random import random
from time import sleep
from yum.misc import setup_locale

# FIXME: is it really sane to use this from here?
sys.path.append('/usr/share/yum-cli')
from output import YumOutput
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

    def updatesAvailable(self, summary):
        """Appends a message to the output list stating that there are
        updates available.

        :param summary: A human-readable summary of the transaction.
        """
        self.output.append('The following updates are available on %s:' % self.opts.system_name)
        self.output.append(summary)

    def updatesDownloading(self, summary):
        """Append a message to the output list stating that
        downloading updates has started.

        :param summary: A human-readable summary of the transaction.
        """
        self.output.append('The following updates will be downloaded on %s:' % self.opts.system_name)
        self.output.append(summary)

    def updatesDownloaded(self):
        """Append a message to the output list stating that updates
        have been downloaded successfully.
        """
        self.output.append("Updates downloaded successfully.")

    def updatesInstalling(self, summary):
        """Append a message to the output list stating that
        installing updates has started.

        :param summary: A human-readable summary of the transaction.
        """
        self.output.append('The following updates will be applied on %s:' % self.opts.system_name)
        self.output.append(summary)

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


class EmailEmitter(UpdateEmitter):
    """Emitter class to send messages via email."""

    def __init__(self, opts):
        super(EmailEmitter, self).__init__(opts)        
        self.subject = ""

    def updatesAvailable(self, summary):
        """Appends a message to the output list stating that there are
        updates available, and set an appropriate subject line.

        :param summary: A human-readable summary of the transaction.
        """
        super(EmailEmitter, self).updatesAvailable(summary)
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
        # Don't send empty emails
        if not self.output:
            return
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
        # Don't print blank lines
        if not self.output:
            return
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
    update_cmd = Option("default")
    apply_updates = BoolOption(False)
    download_updates = BoolOption(False)
    yum_config_file = Option("/etc/yum.conf")
    group_list = ListOption([])
    group_package_types = ListOption(['mandatory', 'default'])


class YumCronBase(yum.YumBase, YumOutput):
    """Main class to check for and apply the updates."""

    def __init__(self, config_file_name = None):
        """Create a YumCronBase object, and perform initial setup.

        :param config_file_name: a String specifying the name of the
           config file to use.
        """
        yum.YumBase.__init__(self)
        YumOutput.__init__(self)

        # Read the config file
        self.readConfigFile(config_file_name)
        self.term.reinit(color='never')
        self.term.columns = self.opts.output_width


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
            print >> sys.stderr, "Error reading config file:", config_file_name
            sys.exit(1)

        # Populate the values into  the opts object
        self.opts.populate(confparser, 'commands')
        self.opts.populate(confparser, 'emitters')
        self.opts.populate(confparser, 'email')
        self.opts.populate(confparser, 'groups')
        self._confparser = confparser

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

            # This needs to be set early, errors are handled later.
            try: level = int(self._confparser.get('base', 'debuglevel'))
            except: level = -2
            self.preconf.debuglevel = level
            if -4 <= level <= -2:
                self.preconf.errorlevel = level + 4

            # if we are not root do the special subdir thing
            if os.geteuid() != 0:
                self.setCacheDir()

            # override base yum options
            self.conf.populate(self._confparser, 'base')
            del self._confparser

        except Exception, e:
            # If there are any exceptions, send a message about them,
            # and return False
            self.emitSetupFailed('%s' % e)
            sys.exit(1)

    def acquireLock(self):
        """ Wrapper method around doLock to emit errors correctly."""

        try:
            self.doLock()
        except yum.Errors.LockError, e:
            self.logger.warn("Failed to acquire the yum lock: %s", e)
            sys.exit(1)

    def populateUpdateMetadata(self):
        """Populate the metadata for the packages in the update."""

        for repo in self.repos.sort():
            repo.metadata_expire = 0
            repo.skip_if_unavailable = True

        self.pkgSack # honor skip_if_unavailable
        self.upinfo

    def refreshUpdates(self):
        """Check whether updates are available.

        :return: Boolean indicating whether any updates are
           available
        """
        try:
            #  Just call .update() because it does obsoletes loops, and group
            # objects. etc.

            update_cmd = self.opts.update_cmd
            idx = update_cmd.find("security-severity:")
            if idx != -1:
                sevs       = update_cmd[idx + len("security-severity:"):]
                update_cmd = update_cmd[:idx + len("security")]
                self.updateinfo_filters['sevs'] = sevs.split(",")


            if self.opts.update_cmd in ('minimal', 'minimal-security'):
                if not yum.updateinfo.update_minimal(self):
                    return False
                self.updateinfo_filters['bugfix'] = True
            elif self.opts.update_cmd in ('default', 'security',
                                          'default-security'):
                if not self.update():
                    return False
            else:
                # return False ?
                self.opts.update_cmd = 'default'
                if not self.update():
                    return False

            if self.opts.update_cmd.endswith("security"):
                self.updateinfo_filters['security'] = True
                yum.updateinfo.remove_txmbrs(self)
            elif self.opts.update_cmd == 'minimal':
                self.updateinfo_filters['bugfix'] = True
                yum.updateinfo.remove_txmbrs(self)

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
            self.conf.downloadonly = not self.opts.apply_updates
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

        if emit :
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

        # Exit if we don't need to check for updates
        if not (self.opts.update_messages or self.opts.download_updates or self.opts.apply_updates):
            sys.exit(0)

        # Check for updates in packages, or groups ... need to run both.
        pups = self.refreshUpdates()
        gups = self.refreshGroupUpdates()
        # If neither have updates, we can just exit.
        if not (pups or gups):
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

        self.installUpdates(self.opts.update_messages)

        self.releaseLocks()
        sys.exit(0)

    def releaseLocks(self):
        """Close the rpm database, and release the yum lock."""
        self.closeRpmDB()
        self.doUnlock()

    def emitAvailable(self):
        """Emit a notice stating whether updates are available."""
        summary = self.listTransaction()
        map(lambda x: x.updatesAvailable(summary), self.emitters)

    def emitDownloading(self):
        """Emit a notice stating that updates are downloading."""
        summary = self.listTransaction()
        map(lambda x: x.updatesDownloading(summary), self.emitters)

    def emitDownloaded(self):
        """Emit a notice stating that updates have downloaded."""
        map(lambda x: x.updatesDownloaded(), self.emitters)

    def emitInstalling(self):
        """Emit a notice stating that automatic updates are about to
        be applied.
        """
        summary = self.listTransaction()
        map(lambda x: x.updatesInstalling(summary), self.emitters)

    def emitInstalled(self):
        """Emit a notice stating that automatic updates have been applied."""
        map(lambda x: x.updatesInstalled(), self.emitters)

    def emitSetupFailed(self, error):
        """Emit a notice stating that checking for updates failed."""
        map(lambda x: x.setupFailed(error), self.emitters)

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
    setup_locale(override_time=True)
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
