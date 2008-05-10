#!/usr/bin/python
# -*- encoding: utf-8 -*-
"""
  Script to run notify-send on highlights and private messages.
  Highlights are configured via weechat itself, this script has only
  two configuration variables:
    `time`    - time to show message
    `icon`    - icon to set for notify-send
  Supports non-english messages, channels, nicknames as soon as weechat
  knows about them with recode plugin.
  It also supports (somehow) finding correct DBUS_SESSION_BUS_ADDRESS on
  linux in runtime, so if you run weechat in screen, kill X and reattach,
  everything will still run without issues.

  The script is in the public domain.
  Leonid Evdokimov (weechat at darkk dot net dot ru)
  http://darkk.net.ru/weechat/weenotify.py

0.01
0.02 - versions written by Pawel Pogorzelski
0.3  - initial commit
0.4  - notify-send requires escaping of «<» -> &lt; etc.
       at least that's valid for x11-misc/notification-daemon-0.3.7
"""

import weechat
import re
import os
import errno
import xml.sax.saxutils as saxutils
from itertools import ifilter
from subprocess import Popen
from locale import getlocale

class OsSupport(object):
    def _not_implemented(self):
        raise NotImplementedError, "your OS is not supported"
    def get_environment(self, pid):
        return self._not_implemented()
    def get_exe_fname(self, pid):
        return self._not_implemented()
    def getppid(self, pid = None):
        return self._not_implemented()
    def listpids(self):
        return self._not_implemented()
    def get_parents(self, pid = None):
        if pid is None:
            pid = os.getpid()
        l = [pid]
        while pid != 1:
            pid = self.getppid(pid)
            if pid == 0: # `[kthreadd]` and `init` have ppid == 0 at linux
                break
            l.append(pid)
        return l



class LinuxSupport(OsSupport):
    def get_environment(self, pid):
        return dict((line.split('=', 1) for line in open('/proc/%i/environ' % pid).read().split('\x00') if line))

    def get_exe_fname(self, pid):
        try:
            argv0 = os.readlink('/proc/%i/exe' % pid)
        except OSError, e:
            if e.errno == errno.EACCES:
                argv0 = open('/proc/%i/cmdline' % pid).read().split('\x00', 1)[0]
            else:
                raise
        return os.path.split(argv0)[1]

    def getppid(self, pid):
        for line in open('/proc/%i/status' % pid):
            match = re.match(r'PPid:\s+(\d+)', line)
            if match:
                return int(match.group(1))
        raise Exception, "No parent pid"

    def listpids(self):
        def is_int(s):
            try:
                int(s)
                return True
            except:
                return False
        for pid in (int(s) for s in ifilter(is_int, os.listdir('/proc'))):
            yield pid



def get_dbus_addr():
    X = set()
    for pid in weeos.listpids():
        if weeos.get_exe_fname(pid) == 'X':
            X.add(pid)

    if not X:
        return None

    init_children = set()
    for pid in X:
        init_children.add(weeos.get_parents(pid)[-2])

    for pid in weeos.listpids():
        try:
            if weeos.get_parents(pid)[-2] in init_children:
                return weeos.get_environment(pid)['DBUS_SESSION_BUS_ADDRESS']
        except:
            continue
    return None


dbus_addr = None
def update_dbus_addr():
    global dbus_addr
    try:
        dbus_addr = get_dbus_addr()
    except NotImplementedError, e:
        weechat.prnt('Dynamic DBUS_SESSION_BUS_ADDRESS detection is not supported on your OS: %s', str(e))
        dbus_addr = os.getenv('DBUS_SESSION_BUS_ADDRESS')
    weechat.prnt("We're running under X, dbus addrress is %s" % dbus_addr, '', '')


def run_notify(nick, chan, message):
    # FIXME: possible bug if notify-send loops
    args = ['notify-send']
    delay = int(weechat.get_plugin_config('time')) * 1000
    if delay:
        args.extend(['-t', str(delay)])
    icon = weechat.get_plugin_config('icon')
    if icon and os.path.exists(icon):
        args.extend(['-i', icon])
    args.extend([saxutils.escape(s) for s in ('--', u'%s wrote to %s' % (nick, chan), message)])
    args = [s.encode(local_charset) for s in args]
    newenv = {'DBUS_SESSION_BUS_ADDRESS': dbus_addr}
    null = open(os.devnull)
    p = Popen(args, env = newenv, stdout = null, stderr = null)
    if p.wait() != 0:
        update_dbus_addr()
        p = Popen(args, env = newenv)
        p.wait()


def parse_privmsg(server, command):
    # :nick!ident@host PRIVMSG dest :foobarbaz
    l = command.split(' ', 3)
    mask = l[0][1:]
    nick = mask.split("!")[0]
    dest = l[2]
    message = l[3][1:]
    ###########################################
    #nothing, info, message = command.split(":", 2)
    #info = info.split(' ')
    if dest == weechat.get_info('nick', server):
        buffer = nick
    else:
        buffer = dest
    return (nick, buffer, message)

def strip_irc_colors(message):
    # look at src/plugins/irc/irc-color.c to get proper color parser
    # modifiers = ( # one-byte modifiers
    #    ur'\x02',  # IRC_COLOR_BOLD_CHAR
    #    ur'\x03',  # IRC_COLOR_COLOR_CHAR, color defenition follows
    #    ur'\x0F',  # IRC_COLOR_RESET_CHAR
    #    ur'\x11',  # IRC_COLOR_FIXED_CHAR
    #    ur'\x12',  # IRC_COLOR_REVERSE_CHAR
    #    ur'\x16',  # IRC_COLOR_REVERSE2_CHAR
    #    ur'\x1d',  # IRC_COLOR_ITALIC_CHAR
    #    ur'\x1f')  # IRC_COLOR_UNDERLINE_CHAR
    # hope, python regexps are character-aware, not byte-aware
    return re.sub(ur'(?:\x02|\x03(?:\d{1,2})?(?:,\d{1,2)?|\x0F|\x11|\x12|\x16|\x1d|\x1f)', '', message)


# weechat does not fire highlight callback on direct PRIVMSG's (aka, «privates» or «queries»)
# but in case of channel highlight BOTH weechat_pv and weechat_highlight are fired
# Say NO to duplications
last_message = None
def on_msg(server, args):
    global last_message
    nick, buffer, message = [unicode(s, local_charset) for s in parse_privmsg(server, args)]

    if message != last_message:
        last_message = message

        match = re.match(ur'\x01ACTION (.*)\x01', message)
        if match:
            message = u'/me ' + match.group(1)

        message = strip_irc_colors(message)

        if nick == buffer:
            buffer = u'me'

        run_notify(nick, buffer, message)
    return weechat.PLUGIN_RC_OK


def main():
    global weeos, local_charset

    default = {
            "time": "3",
            "icon": "/usr/share/pixmaps/gnome-irc.png"
            }

    if weechat.register("weenotify", "0.4", "", "notify-send on highlight/private msg"):
        for k, v in default.items():
            if not weechat.get_plugin_config(k):
                weechat.set_plugin_config(k, v)

    local_charset = getlocale()[1]

    if os.uname()[0] == 'Linux':
        weeos = LinuxSupport()
    else:
        weeos = OsSupport()
    
    update_dbus_addr()
    weechat.add_message_handler("weechat_highlight", "on_msg")
    weechat.add_message_handler("weechat_pv", "on_msg")

main()

# vim:set tabstop=4 softtabstop=4 shiftwidth=4: 
# vim:set foldmethod=marker foldlevel=32 foldmarker={{{,}}}: 
# vim:set expandtab: 
