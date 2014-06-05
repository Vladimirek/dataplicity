from __future__ import absolute_import

from dataplicity.app.subcommand import SubCommand
from dataplicity.app import comms
from dataplicity.client import Client
from dataplicity.client.exceptions import ForceRestart, ClientException
from dataplicity import constants
from dataplicity.client import settings

from daemon import DaemonContext
from daemon.pidfile import TimeoutPIDLockFile

import sys
import os
import time
import socket
from threading import Event, Thread
from os.path import abspath
import logging


log = logging.getLogger('dataplicity')


class Daemon(object):
    """Dataplicity device management process"""

    def __init__(self,
                 conf_path=None,
                 foreground=False,
                 debug=False):
        self.conf_path = conf_path
        self.foreground = foreground
        self.debug = debug

        self.log = logging.getLogger('dataplicity')

        client = self.client = Client(conf_path,
                                      check_firmware=not foreground,
                                      log=self.log)
        conf = client.conf

        self.poll_rate_seconds = conf.get_float("daemon", "poll", 60.0)
        self.last_check_time = None
        self.pid_path = abspath(conf.get('daemon', 'pidfile', '/var/run/dataplicity.pid'))
        self.pipe_path = abspath(conf.get('daemon', 'pipe', '/tmp/dataplicitypipe'))

        self.server_closing_event = Event()

        # Command to execute with the daemon exits
        self.exit_command = None
        self.exit_event = Event()

    def _push_wait(self, client, event, sync_func):
        client.connect_wait(event, sync_func)

    def exit(self, command=None):
        """Exit daemon now, and run optional command"""
        self.exit_command = command
        self.exit_event.set()

    def start(self):

        pipe = None
        if self.pipe_path:
            try:
                os.mkfifo(self.pipe_path)
                self.log.debug("create named pipe '{}'".format(self.pipe_path))
            except:
                pass

            try:
                pipe = os.open(self.pipe_path, os.O_RDONLY | os.O_NONBLOCK)
            except:
                self.log.exception('unable to open pipe')
                pass
            else:
                self.log.debug('opened pipe "{}" ({})'.format(self.pipe_path, pipe))

        self.log.debug('starting dataplicity service with conf {}'.format(self.conf_path))

        #server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        self.client.tasks.start()
        self.log.debug('ready')
        sync_push_thread = Thread(target=self._push_wait,
                                  args=(self.client,
                                        self.server_closing_event,
                                        self.sync_now))
        sync_push_thread.daemon = True
        try:
            sync_push_thread.start()

            try:
                while not self.exit_event.is_set():
                    try:
                        self.poll(time.time())
                    except ClientException:
                        raise
                    except Exception as e:
                        self.log.exception('error in poll')

                    start = time.time()
                    # Loop until another poll is due, or exit is requested
                    while time.time() - start < self.poll_rate_seconds and not self.exit_event.is_set():
                        if pipe is not None:
                            command = os.read(pipe, 128)
                            if command:
                                command = command.splitlines()[-1].rstrip('\n')
                                self.log.debug('command pipe received {}'.format(command))
                                success = self.on_client_command(command)
                        self.exit_event.wait(0.1)

            except SystemExit:
                self.log.debug("exit requested")
                return

            except KeyboardInterrupt:
                self.log.debug("user Exit")
                return

        except ForceRestart:
            self.log.info('restarting...')
            self.exit(' '.join(sys.argv))

        except Exception as e:
            self.log.exception('error in daemon main loop')

        finally:
            if pipe:
                try:
                    os.close(pipe)
                except:
                    self.log.exception('error closing pipe')

            self.log.debug("closing")
            self.server_closing_event.set()
            self.client.tasks.stop()
            self.log.debug("goodbye")

            if self.exit_event.is_set() and self.exit_command is not None:
                time.sleep(1)  # Maybe redundant
                self.log.debug("Executing %s" % self.exit_command)
                os.system(self.exit_command)


    def poll(self, t):
        self.sync_now(t)

    def sync_now(self, t=None):
        if t is None:
            t = time.time()
        try:
            self.client.sync()
        except ClientException:
            raise
        except Exception:
            self.log.exception('sync failed')

    def on_client_command(self, command):
        if command == 'RESTART':
            self.log.info('restart requested')
            self.exit(' '.join(sys.argv))
            return True

        elif command == 'STOP':
            self.log.info('stop requested')
            self.exit()
            return True

        elif command == "SYNC":
            self.log.info('sync requested')
            try:
                self.sync_now()
            except Exception as e:
                return self.log.exception('error on_client_command SYNC')
            else:
                return True

        elif command == "STATUS":
            self.log.info('status requested')
            return True

        return False


class D(SubCommand):
    """Run a Dataplicity daemon process"""
    help = """Run the Dataplicity daemon"""

    @property
    def comms(self):
        return comms.Comms()

    def add_arguments(self, parser):
        parser.add_argument('-f', '--foreground', dest='foreground', action="store_true", default=False,
                            help="run daemon in foreground")
        parser.add_argument('-s', '--stop', dest="stop", action="store_true", default=False,
                            help="stop the daemon")
        parser.add_argument('-r', '--restart', dest='restart', action="store_true",
                            help="restart running daemon")
        parser.add_argument('-t', '--status', dest="status", action="store_true",
                            help="status of the daemon")
        parser.add_argument('-y', '--sync', dest="sync", action="store_true", default=False,
                            help="sync now")

    def get_conf(self):
        conf_path = self.args.conf or constants.CONF_PATH
        conf_path = abspath(conf_path)
        conf = settings.read(conf_path)
        return conf

    def make_daemon(self, debug=None):
        conf_path = self.args.conf or constants.CONF_PATH
        conf_path = abspath(conf_path)

        conf = settings.read(conf_path)
        firmware_conf_path = conf.get('daemon', 'conf', conf_path)
        # It may not exist if there is no installed firmware
        if os.path.exists(firmware_conf_path):
            log.error("daemon firmware conf '{}' does not exist".format(firmware_conf_path))
            conf_path = firmware_conf_path

        if debug is None:
            debug = self.args.debug or self.args.foreground

        self.app.init_logging(self.app.args.logging,
                              foreground=self.args.foreground)
        dataplicity_daemon = Daemon(conf_path,
                                    foreground=self.args.foreground,
                                    debug=debug)
        return dataplicity_daemon

    def run(self):
        args = self.args

        if args.restart:
            self.comms.restart()
            return 0

        if args.stop:
            self.comms.stop()
            return 0

        if args.sync:
            self.comms.sync()
            return 0

        if args.status:
            if self.comms.status():
                sys.stdout.write('running\n')
                return 0
            else:
                sys.stdout.write('not running\n')
                return 1

        try:
            if args.foreground:
                dataplicity_daemon = self.make_daemon()
                dataplicity_daemon.start()
            else:
                conf = self.get_conf()
                pid_path = conf.get('daemon', 'pid_path', constants.PID_PATH)
                if not os.access(pid_path, os.R_OK):
                    sys.stderr.write('no permission to access pid file "{}"\n'.format(pid_path))
                    return -1
                daemon_context = DaemonContext(pidfile=TimeoutPIDLockFile(pid_path, 1), stderr=sys.stderr)
                with daemon_context:
                    dataplicity_daemon = self.make_daemon()
                    dataplicity_daemon.start()

        except Exception, e:
            from traceback import print_exc
            print_exc(e)
            return -1
