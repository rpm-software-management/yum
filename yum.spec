%define move_yum_conf_back 1
%define auto_sitelib 1
%define yum_updatesd 0
%define disable_check 0
%define yum_cron_systemd 1
%define yum_makecache_systemd 1

%if 0%{?rhel}
# If we are building for RHEL ...

%if 0%{?rhel} <= 6
# rhel-6 doesn't have the systemd stuff...
%define yum_cron_systemd 0
%endif

%if 0%{?rhel} <= 7
# rhel-7 doesn't use systemd timers...
%define yum_makecache_systemd 0
%endif

# END OF: If we are building for RHEL ...
%endif


%if 0%{?fedora}
# If we are building for Fedora ...

# we don't have this as of RHEL-7.0.
BuildRequires: bash-completion

%if 0%{?fedora} <= 18
# yum in Fedora <= 18 doesn't use systemd unit files...
%define yum_cron_systemd 0
%endif

%if 0%{?fedora} <= 19
# Don't use .timer's before 20, maybe 19?
%define yum_makecache_systemd 0
%endif

# END OF: If we are building for Fedora ...
%endif


%if %{auto_sitelib}

%{!?python_sitelib: %define python_sitelib %(python -c "from distutils.sysconfig import get_python_lib; print get_python_lib()")}

%else
%define python_sitelib /usr/lib/python?.?/site-packages
%endif

# We always used /usr/lib here, even on 64bit ... so it's a bit meh.
%define yum_pluginslib   /usr/lib/yum-plugins
%define yum_pluginsshare /usr/share/yum-plugins

# disable broken /usr/lib/rpm/brp-python-bytecompile
%define __os_install_post %{nil}
%define compdir %(pkg-config --variable=completionsdir bash-completion)
%if "%{compdir}" == ""
%define compdir "/etc/bash_completion.d"
%endif

Summary: RPM package installer/updater/manager
Name: yum
Version: 3.4.3
Release: 0
License: GPLv2+
Group: System Environment/Base
Source: %{name}-%{version}.tar.gz
URL: http://yum.baseurl.org/
BuildArchitectures: noarch
BuildRequires: python
BuildRequires: gettext
BuildRequires: intltool
%if %{yum_makecache_systemd}
BuildRequires: systemd-units
%endif
# This is really CheckRequires ...
BuildRequires: python-nose
BuildRequires: python >= 2.4
BuildRequires: rpm-python, rpm >= 0:4.4.2
BuildRequires: python-iniparse
BuildRequires: python-urlgrabber >= 3.10-8
BuildRequires: yum-metadata-parser >= 1.1.0
BuildRequires: python2-gpg
# End of CheckRequires
Conflicts: pirut < 1.1.4
Requires: python >= 2.4
Requires: rpm-python, rpm >= 0:4.4.2
Requires: python-iniparse
Requires: python-urlgrabber >= 3.10-8
Requires: yum-metadata-parser >= 1.1.0
Requires: python2-gpg
# rawhide is >= 0.5.3-7.fc18 ... as this is added.
Requires: pyliblzma
# Not really a suggests anymore, due to metadata using it.
Requires: pyxattr
# Suggests, needed for yum fs diff
Requires: diffutils
Requires: cpio

Conflicts: rpm >= 5-0
# Zif is a re-implementation of yum in C, however:
#
# 1. There is no co-operation/etc. with us.
# 2. It touches our private data directly.
#
# ...both of which mean that even if there were _zero_ bugs in zif, we'd
# never be able to change anything after the first user started using it. And
# of course:
#
# 3. Users will never be able to tell that it isn't weird yum bugs, when they
# hit them (and we'll probably never be able to debug them, without becoming
# zif experts).
#
# ...so we have two sane choices: i) Conflict with it. 2) Stop developing yum.
#
#  Upstream says that #2 will no longer be true after this release.
Conflicts: zif <= 0.1.3-3.fc15

Obsoletes: yum-skip-broken <= 1.1.18
Provides: yum-skip-broken = 1.1.18.yum
Obsoletes: yum-basearchonly <= 1.1.9
Obsoletes: yum-plugin-basearchonly <= 1.1.9
Provides: yum-basearchonly = 1.1.9.yum
Provides: yum-plugin-basearchonly = 1.1.9.yum
Obsoletes: yum-allow-downgrade < 1.1.20-0
Obsoletes: yum-plugin-allow-downgrade < 1.1.22-0
Provides: yum-allow-downgrade = 1.1.20-0.yum
Provides: yum-plugin-allow-downgrade = 1.1.22-0.yum
Obsoletes: yum-plugin-protect-packages < 1.1.27-0
Provides: yum-protect-packages = 1.1.27-0.yum
Provides: yum-plugin-protect-packages = 1.1.27-0.yum
Obsoletes: yum-plugin-download-order <= 0.2-2
Obsoletes: yum-plugin-downloadonly <= 1.1.31-7.fc18
Provides: yum-plugin-downloadonly = 3.4.3-44.yum
Obsoletes: yum-presto < 3.4.3-66.yum
Provides: yum-presto = 3.4.3-66.yum
Obsoletes: yum-plugin-security < 1.1.32
Provides: yum-plugin-security = 3.4.3-84.yum
BuildRoot: %{_tmppath}/%{name}-%{version}-%{release}-root-%(%{__id_u} -n)


%description
Yum is a utility that can check for and automatically download and
install updated RPM packages. Dependencies are obtained and downloaded 
automatically, prompting the user for permission as necessary.

%package updatesd
Summary: Update notification daemon
Group: Applications/System
Requires: yum = %{version}-%{release}
Requires: dbus-python
Requires: pygobject2
Requires(preun): /sbin/chkconfig
Requires(post): /sbin/chkconfig
Requires(preun): /sbin/service
Requires(post): /sbin/service
Requires(postun): /sbin/chkconfig
Requires(postun): /sbin/service


%description updatesd
yum-updatesd provides a daemon which checks for available updates and 
can notify you when they are available via email, syslog or dbus. 

%package cron
Summary: RPM package installer/updater/manager cron service
Group: System Environment/Base
Requires: yum >= 3.4.3-84 cronie crontabs findutils
Requires: yum-cron-BE = %{version}-%{release}
# We'd probably like a suggests for yum-cron-daily here.
%if %{yum_cron_systemd}
BuildRequires: systemd-units
Requires(post): systemd
Requires(preun): systemd
Requires(postun): systemd
%else
Requires(post): /sbin/chkconfig
Requires(post): /sbin/service
Requires(preun): /sbin/chkconfig
Requires(preun): /sbin/service
Requires(postun): /sbin/service
%endif

%description cron
These are the files needed to run any of the yum-cron update services.

%package cron-daily
Summary: Files needed to run yum updates as a daily cron job
Group: System Environment/Base
Provides: yum-cron-BE = %{version}-%{release}
Requires: yum-cron > 3.4.3-131

%description cron-daily
This is the configuration file for the daily yum-cron update service, which
lives %{_sysconfdir}/yum/yum-cron.conf.
Install this package if you want auto yum updates nightly via cron (or something
else, via. changing the configuration).
By default this just downloads updates and does not apply them.

%package cron-hourly
Summary: Files needed to run yum updates as an hourly cron job
Group: System Environment/Base
Provides: yum-cron-BE = %{version}-%{release}
Requires: yum-cron > 3.4.3-131

%description cron-hourly
This is the configuration file for the daily yum-cron update service, which
lives %{_sysconfdir}/yum/yum-cron-hourly.conf.
Install this package if you want automatic yum metadata updates hourly via
cron (or something else, via. changing the configuration).

%package cron-security
Summary: Files needed to run security yum updates as once a day
Group: System Environment/Base
Provides: yum-cron-BE = %{version}-%{release}
Requires: yum-cron > 3.4.3-131

%description cron-security
This is the configuration file for the security yum-cron update service, which
lives here: %{_sysconfdir}/yum/yum-cron-security.conf
Install this package if you want automatic yum security updates once a day
via. cron (or something else, via. changing the configuration -- this will be
confusing if it's not security updates anymore though).
By default this will download and _apply_ the security updates, unlike
yum-cron-daily which will just download all updates by default.
This runs after yum-cron-daily, if that is installed.


%prep
%setup -q

%build
make

%if !%{disable_check}
%check
make check
%endif


%install
[ "$RPM_BUILD_ROOT" != "/" ] && rm -rf $RPM_BUILD_ROOT

%if %{yum_cron_systemd}
INIT=systemd
%else
INIT=sysv
%endif

make DESTDIR=$RPM_BUILD_ROOT UNITDIR=%{_unitdir} INIT=$INIT install

install -m 644 %{SOURCE1} $RPM_BUILD_ROOT/%{_sysconfdir}/yum.conf
mkdir -p $RPM_BUILD_ROOT/%{_sysconfdir}/yum/pluginconf.d $RPM_BUILD_ROOT/%{yum_pluginslib}
mkdir -p $RPM_BUILD_ROOT/%{yum_pluginsshare}

%if %{move_yum_conf_back}
# for now, move repodir/yum.conf back
mv $RPM_BUILD_ROOT/%{_sysconfdir}/yum/repos.d $RPM_BUILD_ROOT/%{_sysconfdir}/yum.repos.d
rm -f $RPM_BUILD_ROOT/%{_sysconfdir}/yum/yum.conf
%endif

%if %{yum_updatesd}
echo Keeping local yum-updatesd
%else

# yum-updatesd has moved to the separate source version
rm -f $RPM_BUILD_ROOT/%{_sysconfdir}/yum/yum-updatesd.conf 
rm -f $RPM_BUILD_ROOT/%{_sysconfdir}/rc.d/init.d/yum-updatesd
rm -f $RPM_BUILD_ROOT/%{_sysconfdir}/dbus-1/system.d/yum-updatesd.conf
rm -f $RPM_BUILD_ROOT/%{_sbindir}/yum-updatesd
rm -f $RPM_BUILD_ROOT/%{_mandir}/man*/yum-updatesd*
rm -f $RPM_BUILD_ROOT/%{_datadir}/yum-cli/yumupd.py*

%endif

# Ghost files:
mkdir -p $RPM_BUILD_ROOT/var/lib/yum/history
mkdir -p $RPM_BUILD_ROOT/var/lib/yum/plugins
mkdir -p $RPM_BUILD_ROOT/var/lib/yum/yumdb
touch $RPM_BUILD_ROOT/var/lib/yum/uuid

# rpmlint bogus stuff...
chmod +x $RPM_BUILD_ROOT/%{_datadir}/yum-cli/*.py
chmod +x $RPM_BUILD_ROOT/%{python_sitelib}/yum/*.py
chmod +x $RPM_BUILD_ROOT/%{python_sitelib}/rpmUtils/*.py

%find_lang %name

%if %{yum_cron_systemd}
# Remove the yum-cron sysV stuff to make rpmbuild happy..
rm -f $RPM_BUILD_ROOT/%{_sysconfdir}/rc.d/init.d/yum-cron
%else
# Remove the yum-cron systemd stuff to make rpmbuild happy..
rm -f $RPM_BUILD_ROOT/%{_unitdir}/yum-cron.service
%endif

%if %{yum_makecache_systemd}
cp -a etc/yum-makecache.service $RPM_BUILD_ROOT/%{_unitdir}
cp -a etc/yum-makecache.timer   $RPM_BUILD_ROOT/%{_unitdir}
%endif

%clean
[ "$RPM_BUILD_ROOT" != "/" ] && rm -rf $RPM_BUILD_ROOT


%if %{yum_updatesd}
%post updatesd
/sbin/chkconfig --add yum-updatesd
/sbin/service yum-updatesd condrestart >/dev/null 2>&1
exit 0

%preun updatesd
if [ $1 = 0 ]; then
 /sbin/chkconfig --del yum-updatesd
 /sbin/service yum-updatesd stop >/dev/null 2>&1
fi
exit 0
%endif

%post cron

%if %{yum_cron_systemd}
#systemd_post yum-cron.service
#  Do this manually because it's a fake service for a cronjob, and cronjobs
# are default on atm. This may change in the future.
if [ $1 = 1 ]; then
 systemctl enable yum-cron >/dev/null 2>&1
else
#  Note that systemctl preset is being run here ... but _only_ on initial
# install. So try this...

if [ -f /var/lock/subsys/yum-cron -a -f /etc/rc.d/init.d/yum-cron ]; then
 systemctl enable yum-cron >/dev/null 2>&1
fi
fi

# Also note:
#  systemctl list-unit-files | fgrep yum-cron
%else
# SYSV init post cron
# Make sure chkconfig knows about the service
/sbin/chkconfig --add yum-cron
# if an upgrade:
if [ "$1" -ge "1" ]; then
# if there's a /etc/rc.d/init.d/yum file left, assume that there was an
# older instance of yum-cron which used this naming convention.  Clean 
# it up, do a conditional restart
 if [ -f /etc/init.d/yum ]; then 
# was it on?
  /sbin/chkconfig yum
  RETVAL=$?
  if [ $RETVAL = 0 ]; then
# if it was, stop it, then turn on new yum-cron
   /sbin/service yum stop 1> /dev/null 2>&1
   /sbin/service yum-cron start 1> /dev/null 2>&1
   /sbin/chkconfig yum-cron on
  fi
# remove it from the service list
  /sbin/chkconfig --del yum
 fi
fi 
exit 0
%endif

%preun cron
%if %{yum_cron_systemd}
%systemd_preun yum-cron.service
%else
# SYSV init preun cron
# if this will be a complete removal of yum-cron rather than an upgrade,
# remove the service from chkconfig control
if [ $1 = 0 ]; then
 /sbin/chkconfig --del yum-cron
 /sbin/service yum-cron stop 1> /dev/null 2>&1
fi
exit 0
%endif

%postun cron
%if %{yum_cron_systemd}
%systemd_postun_with_restart yum-cron.service
%else
# SYSV init postun cron

# If there's a yum-cron package left after uninstalling one, do a
# conditional restart of the service
if [ "$1" -ge "1" ]; then
 /sbin/service yum-cron condrestart 1> /dev/null 2>&1
fi
exit 0
%endif

%if %{yum_makecache_systemd}
%post
%systemd_post yum-makecache.timer

%preun
%systemd_preun yum-makecache.timer

%postun
%systemd_postun_with_restart yum-makecache.timer
%endif

%files -f %{name}.lang
%defattr(-, root, root, -)
%doc README AUTHORS COPYING TODO ChangeLog PLUGINS docs/comps.rng
%if %{move_yum_conf_back}
%config(noreplace) %{_sysconfdir}/yum.conf
%dir %{_sysconfdir}/yum.repos.d
%else
%config(noreplace) %{_sysconfdir}/yum/yum.conf
%dir %{_sysconfdir}/yum/repos.d
%endif
%config(noreplace) %{_sysconfdir}/yum/version-groups.conf
%dir %{_sysconfdir}/yum
%dir %{_sysconfdir}/yum/protected.d
%dir %{_sysconfdir}/yum/fssnap.d
%dir %{_sysconfdir}/yum/vars
%config(noreplace) %{_sysconfdir}/logrotate.d/%{name}
%(dirname %{compdir})
%dir %{_datadir}/yum-cli
%{_datadir}/yum-cli/*
%exclude %{_datadir}/yum-cli/completion-helper.py?
%if %{yum_updatesd}
%exclude %{_datadir}/yum-cli/yumupd.py*
%endif
%{_bindir}/yum
%{python_sitelib}/yum
%{python_sitelib}/rpmUtils
%dir /var/cache/yum
%dir /var/lib/yum
%ghost /var/lib/yum/uuid
%ghost /var/lib/yum/history
%ghost /var/lib/yum/plugins
%ghost /var/lib/yum/yumdb
%{_mandir}/man*/yum.*
%{_mandir}/man*/yum-shell*
# plugin stuff
%dir %{_sysconfdir}/yum/pluginconf.d 
%dir %{yum_pluginslib}
%dir %{yum_pluginsshare}
%if %{yum_makecache_systemd}
%{_unitdir}/yum-makecache.service
%{_unitdir}/yum-makecache.timer
%endif

%files cron
%defattr(-,root,root)
%doc COPYING
%{_sysconfdir}/cron.daily/0yum-daily.cron
%{_sysconfdir}/cron.hourly/0yum-hourly.cron
%config(noreplace) %{_sysconfdir}/yum/yum-cron.conf
%config(noreplace) %{_sysconfdir}/yum/yum-cron-hourly.conf
%if %{yum_cron_systemd}
%{_unitdir}/yum-cron.service
%else
%{_sysconfdir}/rc.d/init.d/yum-cron
%endif
%{_sbindir}/yum-cron
%{_mandir}/man*/yum-cron.*

%files cron-daily
%defattr(-,root,root)
%{_sysconfdir}/cron.daily/0yum-daily.cron
%config(noreplace) %{_sysconfdir}/yum/yum-cron.conf

%files cron-hourly
%defattr(-,root,root)
%{_sysconfdir}/cron.hourly/0yum-hourly.cron
%config(noreplace) %{_sysconfdir}/yum/yum-cron-hourly.conf

%files cron-security
%defattr(-,root,root)
%{_sysconfdir}/cron.daily/0yum-security.cron
%config(noreplace) %{_sysconfdir}/yum/yum-cron-security.conf

%if %{yum_updatesd}
%files updatesd
%defattr(-, root, root)
%config(noreplace) %{_sysconfdir}/yum/yum-updatesd.conf
%config %{_sysconfdir}/rc.d/init.d/yum-updatesd
%config %{_sysconfdir}/dbus-1/system.d/yum-updatesd.conf
%{_datadir}/yum-cli/yumupd.py*
%{_sbindir}/yum-updatesd
%{_mandir}/man*/yum-updatesd*
%endif

%changelog
* Thu Jul 28 2011 Matthew Miller <mattdm at mattdm.org>
- reorganize yum-cron to allow more flexible scheduling

* Wed Apr 20 2011 James Antill <james at fedoraproject.org>
- 3.4.1
- umask bug fix.

* Thu Apr 14 2011 James Antill <james at fedoraproject.org>
- 3.4.0

* Wed Jan 12 2011 Seth Vidal <skvidal at fedoraproject.org>
- put yum-cron back into yum and make the subpkg. Thanks To Alec Habig for
  maintaining this so well for so long.

* Fri Jul 30 2010 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.28

* Thu Mar 18 2010 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.27

* Tue Feb  9 2010 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.26

* Tue Oct 13 2009 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.25

* Thu Sep  3 2009 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.24

* Tue May 19 2009 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.23

* Tue Mar 24 2009 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.22

* Wed Jan  7 2009 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.21

* Mon Oct 27 2008 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.20

* Mon Aug 25 2008 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.19

* Thu Aug  7 2008 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.18

* Wed Jul  8 2008 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.17 

* Wed May 14 2008 Seth Vidal <skvidal at fedoraproject.org>
-  3.2.16

* Wed May 14 2008 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.15

* Mon Apr  7 2008 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.14

* Thu Mar 20 2008 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.13

* Mon Mar  3 2008 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.12

* Fri Feb  8 2008 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.11
* Sun Jan 27 2008 James Bowes <jbowes@redhat.com>
- Move the yumupd module to yum-updatesd

* Sat Jan 26 2008 Tim Lauridsen <timlau at fedoraproject.org>
- Added BuildRequires: intltool
- Added -f %%{name}.lang to %%files
- Added %%find_lang %%name to %%install
* Thu Jan 24 2008 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.10

* Thu Jan 24 2008 Seth Vidal <skvidal at fedoraproject.org>
- wee 3.2.9

* Wed Dec 12 2007 Seth Vidal <skvidal at fedoraproject.org>
- add pygpgme dep for new gpg key handling

* Mon Dec  3 2007 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.8

* Fri Oct 12 2007 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.7

* Fri Oct  5 2007 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.6

* Mon Sep 10 2007 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.5

* Tue Aug 28 2007 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.4
- add python-iniparse - it's a dep here but yum will run w/o it

* Fri Jul 20 2007 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.2

* Thu Jun 21 2007 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.1

* Wed May 16 2007 Seth Vidal <skvidal at fedoraproject.org>
- 3.2.0

* Thu Apr 26 2007 Seth Vidal <skvidal at linux.duke.edu>
- 3.1.7

* Tue Apr  3 2007 Seth Vidal <skvidal at linux.duke.edu>
- 3.1.6

* Wed Mar 21 2007 Seth Vidal <skvidal at linux.duke.edu>
- 3.1.5

* Wed Mar  7 2007 Seth Vidal <skvidal at linux.duke.edu>
- 3.1.4

* Thu Mar  1 2007 Seth Vidal <skvidal at linux.duke.edu>
- 3.1.3

* Wed Feb 14 2007 Seth Vidal <skvidal at linux.duke.edu>
- 3.1.2

* Tue Feb  6 2007 Seth Vidal <skvidal at linux.duke.edu>
- 3.1.1

* Sun Jan 21 2007 Seth Vidal <skvidal at linux.duke.edu>
- 3.1.0 

* Wed Oct  4 2006 Seth Vidal <skvidal at linux.duke.edu>
- 3.0

* Fri Sep 29 2006 Seth Vidal <skvidal at linux.duke.edu>
- 2.9.8

* Tue Sep 26 2006 Seth Vidal <skvidal at linux.duke.edu>
- 2.9.7

* Wed Sep  6 2006 Seth Vidal <skvidal at linux.duke.edu>
- 2.9.6

* Mon Aug 21 2006 Seth Vidal <skvidal at linux.duke.edu>
- 2.9.5

* Wed Aug  9 2006 Seth Vidal <skvidal at linux.duke.edu>
- 2.9.4

* Wed Jul 12 2006 Seth Vidal <skvidal at linux.duke.edu>
- 2.9.3

* Tue Jun 27 2006 Jeremy Katz <katzj@redhat.com> 
- add bits for yum-updatesd subpackage

* Tue Jun 27 2006 Seth Vidal <skvidal at linux.duke.edu>
- 2.9.2

* Wed Jun 21 2006 Seth Vidal <skvidal at linux.duke.edu>
- remove libxml2 dep

* Sun Jun 18 2006 Seth Vidal <skvidal at linux.duke.edu>
- 2.9.1

* Tue Mar  7 2006 Seth Vidal <skvidal at linux.duke.edu>
- 2.9.0 - new dev cycle

* Mon Mar  6 2006 Seth Vidal <skvidal at linux.duke.edu>
- 2.6.0

* Wed Feb 22 2006 Seth Vidal <skvidal@phy.duke.edu>
- 2.5.3

* Sun Jan  8 2006 Seth Vidal <skvidal@phy.duke.edu>
- 2.5.1

* Sun Aug 14 2005 Seth Vidal <skvidal@phy.duke.edu>
- 2.5.0

* Fri Aug  5 2005 Seth Vidal <skvidal@phy.duke.edu>
- back to libxml2-python req

* Fri Jul  8 2005 Seth Vidal <skvidal@phy.duke.edu>
- 2.3.4

* Tue Jun 14 2005 Seth Vidal <skvidal@phy.duke.edu>
- 2.3.3

* Wed Apr  6 2005 Seth Vidal <skvidal@phy.duke.edu>
- added python-elementtree dep, remove libxml2 dep

* Mon Apr  4 2005 Seth Vidal <skvidal@phy.duke.edu>
- 2.3.2

* Mon Mar 28 2005 Seth Vidal <skvidal@phy.duke.edu>
- add in the /etc/yum/*.yum yum shell files

* Mon Mar  7 2005 Seth Vidal <skvidal@phy.duke.edu>
- 2.3.1
- get rid of old obsoletes

* Fri Feb 25 2005 Gijs Hollestelle <gijs@gewis.nl>
- Require python-sqlite

* Fri Feb 25 2005 Seth Vidal <skvidal@phy.duke.edu>
- add yum.cron to weekly to clean packages

* Mon Feb 21 2005 Seth Vidal <skvidal@phy.duke.edu>
- new devel branch - 2.3.0

* Tue Jan 25 2005 Seth Vidal <skvidal@phy.duke.edu>
- 2.1.13

* Sat Nov 27 2004 Seth Vidal <skvidal@phy.duke.edu>
- 2.1.12

* Wed Oct 27 2004 Seth Vidal <skvidal@phy.duke.edu>
- 2.1.11


* Tue Oct 19 2004 Seth Vidal <skvidal@phy.duke.edu>
- 2.1.10

* Mon Oct 18 2004 Seth Vidal <skvidal@phy.duke.edu>
- 2.1.9 - paper bag release


* Mon Oct 18 2004 Seth Vidal <skvidal@phy.duke.edu>
- 2.1.8


* Wed Oct 13 2004 Seth Vidal <skvidal@phy.duke.edu>
- update to 2.1.7
- re-include yum-arch w/deprecation notice

* Wed Oct  6 2004 Seth Vidal <skvidal@phy.duke.edu>
- mdcaching code and list changes
- 2.1.6

* Mon Oct  4 2004 Seth Vidal <skvidal@phy.duke.edu>
- 2.1.5

- lots of minor bugfixes and corrections


* Tue Sep 28 2004 Seth Vidal <skvidal@phy.duke.edu>
- 2.1.4

* Fri Sep  3 2004 Seth Vidal <skvidal@phy.duke.edu>
- big depsolver update

* Wed Sep  1 2004 Seth Vidal <skvidal@phy.duke.edu>
- more changes

* Tue Aug 31 2004 Seth Vidal <skvidal@phy.duke.edu>
- all new stuff for 2.1.X

* Mon Sep  8 2003 Seth Vidal <skvidal@phy.duke.edu>
- brown paper-bag 2.0.3

* Sun Sep  7 2003 Seth Vidal <skvidal@phy.duke.edu>
- bump to 2.0.2

* Fri Aug 15 2003 Seth Vidal <skvidal@phy.duke.edu>
- bump to 2.0.1

* Sun Jul 13 2003 Seth Vidal <skvidal@phy.duke.edu>
- bump to 2.0

* Sat Jul 12 2003 Seth Vidal <skvidal@phy.duke.edu>
- made yum.cron config(noreplace)

* Sat Jun  7 2003 Seth Vidal <skvidal@phy.duke.edu>
- add stubs to spec file for rebuilding easily with custom yum.conf and
- yum.cron files

* Sat May 31 2003 Seth Vidal <skvidal@phy.duke.edu>
- bump to 1.98

* Mon Apr 21 2003 Seth Vidal <skvidal@phy.duke.edu>
- bump to 1.97

* Wed Apr 16 2003 Seth Vidal <skvidal@phy.duke.edu>
- moved to fhs compliance
- ver to 1.96

* Mon Apr  7 2003 Seth Vidal <skvidal@phy.duke.edu>
- updated for 1.95 betaish release
- remove /sbin legacy
- no longer starts up by default
- do the find_lang thing

* Sun Dec 22 2002 Seth Vidal <skvidal@phy.duke.edu>
- bumped ver to 0.9.4
- new spec file for rhl 8.0

* Sun Oct 20 2002 Seth Vidal <skvidal@phy.duke.edu>
- bumped ver to 0.9.3

* Mon Aug 26 2002 Seth Vidal <skvidal@phy.duke.edu>
- bumped ver to 0.9.2

* Thu Jul 11 2002 Seth Vidal <skvidal@phy.duke.edu>
- bumped ver to 0.9.1

* Thu Jul 11 2002 Seth Vidal <skvidal@phy.duke.edu>
- bumped ver  to 0.9.0

* Thu Jul 11 2002 Seth Vidal <skvidal@phy.duke.edu>
- added rpm require

* Sun Jun 30 2002 Seth Vidal <skvidal@phy.duke.edu>
- 0.8.9

* Fri Jun 14 2002 Seth Vidal <skvidal@phy.duke.edu>
- 0.8.7

* Thu Jun 13 2002 Seth Vidal <skvidal@phy.duke.edu>
- bumped to 0.8.5

* Thu Jun 13 2002 Seth Vidal <skvidal@phy.duke.edu>
- bumped to 0.8.4

* Sun Jun  9 2002 Seth Vidal <skvidal@phy.duke.edu>
- bumped to 0.8.2
* Thu Jun  6 2002 Seth Vidal <skvidal@phy.duke.edu>
- First packaging
