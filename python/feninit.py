# vi: set tabstop=4 shiftwidth=4 expandtab:
# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1/GPL 2.0/LGPL 2.1
#
# The contents of this file are subject to the Mozilla Public License Version
# 1.1 (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/
#
# Software distributed under the License is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License
# for the specific language governing rights and limitations under the
# License.
#
# The Original Code is Mozilla Corporation code.
#
# The Initial Developer of the Original Code is the Mozilla Corporation.
# Portions created by the Initial Developer are Copyright (C) 2011
# the Initial Developer. All Rights Reserved.
#
# Contributor(s):
#   Jim Chen <jimnchen@gmail.com>
#
# Alternatively, the contents of this file may be used under the terms of
# either the GNU General Public License Version 2 or later (the "GPL"), or
# the GNU Lesser General Public License Version 2.1 or later (the "LGPL"),
# in which case the provisions of the GPL or the LGPL are applicable instead
# of those above. If you wish to allow use of your version of this file only
# under the terms of either the GPL or the LGPL, and not to allow others to
# use your version of this file under the terms of the MPL, indicate your
# decision by deleting the provisions above and replace them with the notice
# and other provisions required by the GPL or the LGPL. If you do not delete
# the provisions above, a recipient may use your version of this file under
# the terms of any one of the MPL, the GPL or the LGPL.
#
# ***** END LICENSE BLOCK *****

import gdb, adb, readinput, adblog, os, sys, subprocess, threading, time, shlex

class FenInit(gdb.Command):
    '''Initialize gdb for debugging Fennec on Android'''

    TASKS = (
        'Debug Fennec (default)',
        'Debug compiled-code unit test'
    )
    (
        TASK_FENNEC,
        TASK_CPP_TEST
    ) = (
        0,
        1
    )

    def __init__(self):
        super(FenInit, self).__init__('feninit', gdb.COMMAND_SUPPORT)

    def complete(self, text, word):
        return gdb.COMPLETE_NONE

    def _chooseTask(self):
        print '\nFennec GDB utilities'
        for i in range(len(self.TASKS)):
            print '%d. %s' % (i + 1, self.TASKS[i])
        task = 0
        while task < 1 or task > len(self.TASKS):
            task = readinput.call('Enter number from above: ', '-l',
                                  str(list(self.TASKS)))
            if not task:
                task = 1
                break
            if task.isdigit():
                task = int(task)
                continue
            matchTask = filter(lambda x: x.startswith(task), self.TASKS)
            if len(matchTask) == 1:
                task = self.TASKS.index(matchTask[0]) + 1
        print ''
        self.task = task - 1

    def _chooseDevice(self):
        dev = adb.chooseDevice()
        print 'Using device %s' % dev
        self.device = dev

    def _chooseObjdir(self):
        def scanSrcDir(objdirs, path):
            # look for 'obj*' directories, using 'dist' as a clue
            abspath = os.path.abspath(os.path.expanduser(path))
            if not os.path.isdir(abspath):
                return
            if os.path.isdir(os.path.join(abspath, 'dist')):
                objdirs.append(abspath)
                return
            for d in os.listdir(abspath):
                if not d.startswith('obj'):
                    continue
                objdir = os.path.join(abspath, d)
                if os.path.isdir(objdir) and \
                        os.path.isdir(os.path.join(objdir, 'dist')):
                    objdirs.append(objdir)

        objdir = '' # None means don't use an objdir
        objdirs = []
        # look for possible locations
        srcroot = self.srcroot if hasattr(self, 'srcroot') else '~'
        scanSrcDir(objdirs, os.path.join(srcroot, 'mozilla-central'))
        scanSrcDir(objdirs, os.path.join(srcroot, 'central'))
        scanSrcDir(objdirs, os.path.join(srcroot, 'mozilla-aurora'))
        scanSrcDir(objdirs, os.path.join(srcroot, 'aurora'))
        scanSrcDir(objdirs, os.path.join(srcroot, 'mozilla-beta'))
        scanSrcDir(objdirs, os.path.join(srcroot, 'beta'))
        scanSrcDir(objdirs, os.path.join(srcroot, 'mozilla-release'))
        scanSrcDir(objdirs, os.path.join(srcroot, 'release'))
        objdirs.sort()

        # use saved setting if possible; also allows gdbinit to set objdir
        if hasattr(self, 'objdir'):
            objdir = self.objdir
            if objdir:
                scanSrcDir(objdirs, objdir)
                if objdir not in objdirs:
                    print 'feninit.default.objdir (%s) is not found' % objdir
            else:
                objdir = None
                objdirs.append(objdir)
        # let user choose even if only one objdir found,
        # because the user might want to not use an objdir
        while objdir not in objdirs:
            print 'Choices for object directory to use:'
            print '0. Do not use object directory'
            for i in range(len(objdirs)):
                print '%d. %s' % (i + 1, objdirs[i])
            print 'Enter number from above or enter alternate path'
            objdir = readinput.call(': ', '-d')
            print ''
            if not objdir:
                continue
            if objdir.isdigit() and int(objdir) >= 0 and \
                    int(objdir) <= len(objdirs):
                if int(objdir) == 0:
                    objdir = None
                else:
                    objdir = objdirs[int(objdir) - 1]
                break
            objdir = os.path.abspath(os.path.expanduser(objdir))
            matchObjdir = filter(lambda x:
                    x.startswith(objdir), objdirs)
            if len(matchObjdir) == 0:
                # not on list, verify objdir first
                scanSrcDir(objdirs, objdir)
            elif len(matchObjdir) == 1:
                # only one match, good to go
                objdir = matchObjdir[0]
        print 'Using object directory: %s' % str(objdir)
        self.objdir = objdir

    def _pullLibsAndSetPaths(self):
        DEFAULT_FILE = 'system/bin/app_process'
        # libraries/binaries to pull from device
        DEFAULT_LIBS = ['system/lib/libdl.so', 'system/lib/libc.so',
                'system/lib/libm.so', 'system/lib/libstdc++.so',
                'system/lib/liblog.so', 'system/lib/libz.so',
                'system/lib/libGLESv2.so', 'system/bin/linker']
        # search path for above libraries/binaries
        DEFAULT_SEARCH_PATHS = ['system/lib', 'system/bin']

        datadir = str(gdb.parameter('data-directory'))
        libdir = os.path.abspath(
                os.path.join(datadir, os.pardir, 'lib', self.device))
        self.datadir = datadir
        self.libdir = libdir
        self.bindir = os.path.abspath(
                os.path.join(datadir, os.pardir, 'bin'))

        # always pull the executable file
        dstpath = os.path.join(libdir, DEFAULT_FILE.replace('/', os.sep))
        if not os.path.exists(dstpath):
            adb.pull('/' + DEFAULT_FILE, dstpath)

        # only pull libs and set paths if automatically loading symbols
        if hasattr(self, 'skipPull') and not self.skipPull:
            sys.stdout.write('Pulling libraries to %s... ' % libdir)
            sys.stdout.flush()
            for lib in DEFAULT_LIBS:
                try:
                    dstpath = os.path.join(libdir, lib.replace('/', os.sep))
                    if not os.path.exists(dstpath):
                        adb.pull('/' + lib, dstpath)
                except gdb.GdbError:
                    sys.stdout.write('\n cannot pull %s... ' % lib)
                    sys.stdout.flush()
            print 'Done'

        gdb.execute('set sysroot ' + libdir, False, True)
        print 'Set sysroot to "%s".' % libdir

        searchPaths = [os.path.join(libdir, d) \
                for d in DEFAULT_SEARCH_PATHS]
        if self.objdir:
            searchPaths.append(os.path.join(self.objdir, 'dist', 'bin'))
            searchPaths.append(os.path.join(self.objdir, 'dist', 'lib'))
        gdb.execute('set solib-search-path ' +
                os.pathsep.join(searchPaths), False, True)
        print 'Updated solib-search-path.'

    def _getPackageName(self):
        if self.objdir:
            acname = os.path.join(self.objdir, 'config', 'autoconf.mk')
            try:
                acfile = open(acname)
                for line in acfile:
                    if 'ANDROID_PACKAGE_NAME' not in line:
                        continue
                    acfile.close()
                    pkg = line.partition('=')[2].strip()
                    print 'Using package %s.' % pkg
                    return pkg
                acfile.close()
            except OSError:
                pass
        pkgs = [x[:x.rindex('-')] for x in \
            adb.call(['shell', 'ls', '-1', '/data/app']).splitlines() \
            if x.startswith('org.mozilla.')]
        if pkgs:
            print 'Found package names:'
            for pkg in pkgs:
                print ' ' + pkg
        else:
            pkgs = ['org.mozilla.fennec_unofficial', 'org.mozilla.fennec',
                    'org.mozilla.aurora', 'org.mozilla.firefox']
        pkg = None
        while not pkg:
            pkg = readinput.call(
                'Use package (e.g. org.mozilla.fennec): ', '-l', str(pkgs))
        print ''
        return pkg

    def _launch(self, pkg):
        # name of child binary
        CHILD_EXECUTABLE = 'plugin-container'

        ps = adb.call(['shell', 'ps']).splitlines()
        # get parent/child processes that are waiting ('S' state)
        pkgProcs = [x for x in ps if pkg in x]

        if all([CHILD_EXECUTABLE in x for x in pkgProcs]):
            # launch
            sys.stdout.write('Launching %s... ' % pkg)
            sys.stdout.flush()
            out = adb.call(['shell', 'am', 'start', '-n', pkg + '/.App'])
            if 'error' in out.lower():
                print ''
                print out
                raise gdb.GdbError('Error while launching %s.' % pkg)

            # FIXME sleep for 1s to allow time to launch
            time.sleep(1)

    def _attach(self, pkg):
        # name of child binary
        CHILD_EXECUTABLE = 'plugin-container'
        # 'file' command argument for parent process
        PARENT_FILE_PATH = os.path.join(self.libdir,
                'system', 'bin', 'app_process')
        # 'file' command argument for child process
        if self.objdir:
            CHILD_FILE_PATH = os.path.join(self.objdir,
                    'dist', 'bin', CHILD_EXECUTABLE)
            if not os.path.exists(CHILD_FILE_PATH):
                CHILD_FILE_PATH = os.path.join(self.objdir,
                        'dist', 'bin', 'lib', 'libplugin-container.so')
        else:
            CHILD_FILE_PATH = None

        ps = adb.call(['shell', 'ps']).splitlines()
        # get parent/child processes that are waiting ('S' state)
        pkgProcs = [x for x in ps if pkg in x]

        # wait for parent launch to complete
        while all([CHILD_EXECUTABLE in x for x in pkgProcs]):
            ps = adb.call(['shell', 'ps']).splitlines()
            # get parent/child processes that are waiting ('S' state)
            pkgProcs = [x for x in ps if pkg in x and
                    ('S' in x.split() or 'T' in x.split())]
        print 'Done'

        # get parent/child(ren) pid's
        pidParent = next((next((col for col in x.split() if col.isdigit()))
                for x in pkgProcs if CHILD_EXECUTABLE not in x))
        pidChild = [next((col for col in x.split() if col.isdigit()))
                for x in pkgProcs if CHILD_EXECUTABLE in x]
        pidChildParent = pidParent

        # see if any gdbserver instance is running, and discard
        # the debuggee from our list because it's already taken
        for proc in [x.split() for x in ps if 'gdbserver' in x]:
            # get the program being debugged by examine gdbserver cmdline
            cmdline = adb.call(['shell', 'cat',
                    '/proc/' + next((col for col in proc if col.isdigit())) +
                    '/cmdline']).split('\0')
            if '--attach' not in cmdline:
                continue
            # this should be the pid
            pid = next((x for x in reversed(cmdline) if x.isdigit()))
            if pid == pidParent:
                pidParent = None
            elif pid in pidChild:
                pidChild.remove(pid)

        if pidParent:
            # the parent is not being debugged, pick the parent
            pidAttach = pidParent
            sys.stdout.write('Attaching to parent (pid %s)... ' % pidAttach)
            sys.stdout.flush()
        elif not pidChild:
            # ok, no child is available. assume the user
            # wants to wait for child to start up
            pkgProcs = None
            print 'Waiting for child process...'
            while not pkgProcs:
                ps = adb.call(['shell', 'ps']).splitlines()
                # check for 'S' state, for right parent, and for right child
                pkgProcs = [x for x in ps if ('S' in x or 'T' in x) and \
                        pidChildParent in x and CHILD_EXECUTABLE in x]
            pidChild = [next((col for col in x.split() if col.isdigit()))
                    for x in pkgProcs]

        # if the parent was not picked, pick the right child
        if not pidParent and len(pidChild) == 1:
            # that is easy
            pidAttach = pidChild[0]
            sys.stdout.write('Attaching to child (pid %s)... ' % pidAttach)
            sys.stdout.flush()
        elif not pidParent:
            # should not happen for now, because we only use one child
            pidAttach = None
            while pidAttach not in pidChild:
                print 'WTF multiple child processes found:'
                for i in range(len(pidChild)):
                    print '%d. pid %s' % (i + 1, pidChild[i])
                pidAttach = readinput.call('Child pid: ', '-l', str(pidChild))
                if pidAttach.isdigit() and int(pidAttach) > 0 \
                        and int(pidAttach) <= len(pidChild):
                    pidAttach = pidChild[pidAttach]
            sys.stdout.write('\nAttaching... ')
            sys.stdout.flush()
        self.pid = pidAttach

        gdbserver_port = ':' + str(self.gdbserver_port
                if hasattr(self, 'gdbserver_port') else 0)
        self._attachGDBServer(
                pkg,
                (PARENT_FILE_PATH if pidParent else CHILD_FILE_PATH),
                ['--attach', gdbserver_port, pidAttach])

        if pidParent:
            print '\nRun another gdb session to debug child process.'
        print '\nReady. Use "continue" to resume execution.'

    def _attachGDBServer(self, pkg, filePath, args,
                         skipShell = False, redirectOut = False):
        # always push gdbserver in case there's an old version on the device
        gdbserverPath = '/data/local/tmp/gdbserver'
        adb.push(os.path.join(self.bindir, 'gdbserver'), gdbserverPath)

        # run this after fork() and before exec(gdbserver)
        # so 'adb shell gdbserver' doesn't get gdb's signals
        def gdbserverPreExec():
            os.setpgrp()

        def runGDBServer(args): # returns (proc, port, stdout)
            proc = adb.call(args, stderr=subprocess.PIPE, async=True,
                    preexec_fn=gdbserverPreExec)
            # we have to find the port used by gdbserver from stdout
            # while this complicates things a little, it allows us to
            # have multiple gdbservers running
            out = []
            line = ' '
            while line:
                line = proc.stdout.readline()
                words = line.split()
                out.append(line.rstrip())
                # kind of hacky, assume the port number comes after 'port'
                if 'port' not in words:
                    continue
                if words.index('port') + 1 == len(words):
                    continue
                port = words[words.index('port') + 1]
                if not port.isdigit():
                    continue
                return (proc, port, None)
            # not found, error?
            return (None, None, out)

        # can we run as root?
        gdbserverProc = None
        if not skipShell:
            gdbserverArgs = ['shell', gdbserverPath]
            gdbserverArgs.extend(args)
            (gdbserverProc, port, gdbserverRootOut) = runGDBServer(gdbserverArgs)
        if not gdbserverProc:
            sys.stdout.write('as non-root... ')
            sys.stdout.flush()
            gdbserverArgs = ['shell', 'run-as', pkg, gdbserverPath]
            gdbserverArgs.extend(args)
            (gdbserverProc, port, gdbserverRunAsOut) = \
                    runGDBServer(gdbserverArgs)
        if not gdbserverProc:
            sys.stdout.write('as root... ')
            sys.stdout.flush()
            gdbserverArgs = [gdbserverPath]
            gdbserverArgs.extend(args)
            adb.call(['shell', 'echo', '#!/bin/sh\n' +
                    ' '.join(gdbserverArgs), '>', gdbserverPath + '.run'])
            adb.call(['shell', 'chmod', '755', gdbserverPath + '.run'])
            (gdbserverProc, port, gdbserverSuOut) = runGDBServer(
                    ['shell', 'su', '-c', gdbserverPath + '.run'])
        if not gdbserverProc:
            print '\n"gdbserver" output:'
            print ' ' + ' '.join(gdbserverRootOut).replace('\0', '')
            print '"run-as" output:'
            print ' ' + ' '.join(gdbserverRunAsOut).replace('\0', '')
            print '"su -c" output:'
            print ' ' + ' '.join(gdbserverSuOut).replace('\0', '')
            raise gdb.GdbError('failed to run gdbserver')

        self.port = port
        self.gdbserver = gdbserverProc

        # collect output from gdbserver in another thread
        def makeGdbserverWait(obj, proc):
            def gdbserverWait():
                if not redirectOut:
                    obj.gdbserverOut = proc.communicate()
                    return
                while proc.poll() == None:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    if adblog.continuing:
                        sys.__stderr__.write('\x1B[1mout> \x1B[22m' + line)
            return gdbserverWait;
        gdbserverThd = threading.Thread(
                name = 'GDBServer',
                target = makeGdbserverWait(self, gdbserverProc))
        gdbserverThd.daemon = True
        gdbserverThd.start()

        # forward the port that gdbserver gave us
        adb.forward('tcp:' + port, 'tcp:' + port)
        print 'Done'

        sys.stdout.write('Setting up remote debugging... ')
        sys.stdout.flush()
        # load the right file
        gdb.execute('file ' + filePath, False, True)
        gdb.execute('target remote :' + port, False, True)
        print 'Done'

    def _chooseCpp(self, pkg):
        cpppath = ''
        while not os.path.isfile(cpppath):
            if self.objdir:
                print 'Enter path of unit test ' + \
                      '(use tab-completion to see possibilities)'
                cpppath = readinput.call(': ', '-f', '-c',
                               os.path.join(self.objdir, 'dist', 'bin'),
                               '--file-mode', '0100',
                               '--file-mode-mask', '0100')
            else:
                print 'Enter path of unit test'
                cpppath = readinput.call(': ', '-f',
                               '--file-mode', '0100',
                               '--file-mode-mask', '0100')
        print ''
        self.cpppath = cpppath

    def _prepareCpp(self, pkg):
        ps = adb.call(['shell', 'ps']).splitlines()
        pkgProcs = [x for x in ps if pkg in x]
        if pkgProcs:
            sys.stdout.write('Restarting %s... ' % pkg);
            sys.stdout.flush()
            adb.call(['shell', 'am', 'force-stop', pkg])
            # wait for fennec to stop
            while pkgProcs:
                ps = adb.call(['shell', 'ps']).splitlines()
                pkgProcs = [x for x in ps if pkg in x]
        else:
            # launch
            sys.stdout.write('Launching %s... ' % pkg)
            sys.stdout.flush()
        out = adb.call(['shell', 'am', 'start', '-n', pkg + '/.App',
                '--es', 'env0', 'MOZ_LINKER_EXTRACT=1'])
        if 'error' in out.lower():
            print '\n' + out
            raise gdb.GdbError('Error while launching %s.' % pkg)
        else:
            while not pkgProcs:
                ps = adb.call(['shell', 'ps']).splitlines()
                pkgProcs = [x for x in ps if pkg in x]
            # FIXME sleep for 3s to allow time to launch
            time.sleep(3)
            print 'Done'

    def _attachCpp(self, pkg):
        cppPath = '/data/local/tmp/' + os.path.basename(self.cpppath)
        wrapperPath = '/data/local/tmp/cpptest.run'
        libPath = '/data/data/' + pkg + '/lib'
        cachePath = '/data/data/' + pkg + '/cache'
        profilePath = '/data/data/' + pkg + '/files/mozilla'

        sys.stdout.write('Attaching to test... ')
        sys.stdout.flush()
        adb.push(self.cpppath, cppPath)
        adb.call(['shell', 'echo', '#!/bin/sh\n' +
                  ' '.join(['LD_LIBRARY_PATH=\\$LD_LIBRARY_PATH:' +
                            libPath + ':' + cachePath, 'exec', '\\$@']),
                  '>', wrapperPath])
        adb.call(['shell', 'chmod', '755', wrapperPath])

        skipShell = False
        if 'mozilla' not in adb.call(['shell', 'ls', profilePath]):
            skipShell = True

        gdbserver_port = ':' + str(self.gdbserver_port
                if hasattr(self, 'gdbserver_port') else 0)
        self._attachGDBServer(pkg, self.cpppath,
                ['--wrapper', 'sh', wrapperPath, '--', gdbserver_port, cppPath],
                skipShell)

        print '\nReady. Use "continue" to start execution.'

    def invoke(self, argument, from_tty):
        try:
            saved_height = gdb.parameter('height')
            saved_height = int(saved_height) if saved_height else 0
            gdb.execute('set height 0') # suppress pagination
            if hasattr(self, 'gdbserver') and self.gdbserver:
                if self.gdbserver.poll() is None:
                    print 'Already in remote debug mode.'
                    return
                delattr(self, 'gdbserver')
            self._chooseTask()
            self._chooseDevice()
            self._chooseObjdir()
            self._pullLibsAndSetPaths()
            
            pkg = self._getPackageName()
            if self.task == self.TASK_FENNEC:
                no_launch = hasattr(self, 'no_launch') and self.no_launch
                if not no_launch:
                    self._launch(pkg)
                self._attach(pkg)
            elif self.task == self.TASK_CPP_TEST:
                self._chooseCpp(pkg)
                self._prepareCpp(pkg)
                self._attachCpp(pkg)

            self.dont_repeat()
        except:
            # if there is an error, a gdbserver might be left hanging
            if hasattr(self, 'gdbserver') and self.gdbserver:
                if self.gdbserver.poll() is None:
                    self.gdbserver.terminate()
                    print 'Terminated gdbserver.'
                delattr(self, 'gdbserver')
            raise
        finally:
            gdb.execute('set height ' + str(saved_height), False, False)

default = FenInit()

