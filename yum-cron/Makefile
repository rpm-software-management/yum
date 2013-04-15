UNITDIR=/lib/systemd/system

INIT=systemd

all:
	echo "Nothing to do"

clean:
	rm -f *~

install: install-$(INIT)

install-systemd: install-common
	install -D -m 644 yum-cron.service $(DESTDIR)/$(UNITDIR)/yum-cron.service

install-sysv: install-common
	install -D -m 755 yum-cron.sysvinit $(DESTDIR)/etc/rc.d/init.d/yum-cron

install-common:
	mkdir -p $(DESTDIR)/etc/cron.hourly
	mkdir -p $(DESTDIR)/etc/cron.daily
	mkdir -p $(DESTDIR)/etc/rc.d/init.d
	mkdir -p $(DESTDIR)/usr/sbin
# Install as 0yum-*.cron so it runs before items like
# manpage update, mlocate, and prelink
	install -D -m 755 yum-daily.cron.sh $(DESTDIR)/etc/cron.daily/0yum-daily.cron
	install -D -m 755 yum-hourly.cron.sh $(DESTDIR)/etc/cron.hourly/0yum-hourly.cron
	install -D -m 755 yum-cron.py $(DESTDIR)/usr/sbin/yum-cron
