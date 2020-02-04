#!/usr/bin/python
import sys
try:
    import yum
except ImportError:
    print("""\
There was a problem importing one of the Python modules
required to run yum. The error leading to this problem was:

   %s

Please install a package which provides this module, or
verify that the module is installed correctly.

It's possible that the above module doesn't match the
current version of Python, which is:
%s

If you cannot solve this problem yourself, please go to 
the yum faq at:
  http://yum.baseurl.org/wiki/Faq
  
""" % (sys.exc_info()[1], sys.version), file=sys.stderr)
    sys.exit(1)

sys.path.insert(0, '/usr/share/yum-cli')
try:
    import yummain
    yummain.user_main(sys.argv[1:], exit_code=True)
except KeyboardInterrupt as e:
    print("\n\nExiting on user cancel.", file=sys.stderr)
    sys.exit(1)
