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
import SocketServer
from threading import Event, Thread
from os.path import abspath
import logging


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

        self.server_closing_event = Event()

        # Command to execute with the daemon exits
        self.exit_command = None
        self.exit_event = Event()
        self._server = None
        self._server_thread = None

    def _server_loop(self):
        class daemonTCPHandler(SocketServer.BaseRequestHandler):
            def handle(self):
                self.log = logging.getLogger('dataplicity')
                self.log.debug("Process Daemon command request")
                try:
                    command = self.request.recv(128).rstrip('\n')
                    if command:
                        self.log.debug("Invoke command (%s)" % command)
                        reply = self.server.DP_daemon.on_client_command(command)
                        self.log.debug("Got response (%s)" % reply)
                        if reply is not None:
                            self.request.sendall(reply.rstrip('\n') + '\n')
                except socket.error:
                    pass

        self._server = SocketServer.TCPServer(('127.0.0.1', 8888), daemonTCPHandler)
        self._server.DP_daemon = self
        self.log.debug("Start daemon at 8888")
        self._server.serve_forever()
        self.log.debug("Stopped daemon at 8888")

    def _push_wait(self, client, event, sync_func):
        client.connect_wait(event, sync_func)

    def exit(self, command=None):
        """Exit daemon now, and run optional command"""
        self.exit_command = command
        self.exit_event.set()

    def start(self):

        self.log.debug('starting dataplicity service with conf {}'.format(self.conf_path))

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
                self._server_thread = Thread( target=self._server_loop)
                self._server_thread.start()

            except:
                self.log.exception('unable to start dataplicity daemon')
                return -1

            try:
                while not self.exit_event.isSet():
                    try:
                        self.poll(time.time())
                    except ClientException:
                        raise
                    except Exception as e:
                        self.log.exception('error in poll')
                    time.sleep(1)

            except SystemExit:
                self.log.debug("exit requested")

            except KeyboardInterrupt:
                self.log.debug("user Exit")

            self._stop_threads()
            return

        except ForceRestart:
            self.log.info('restarting...')
            self._stop_threads()
            self.exit(' '.join(sys.argv))

        except Exception as e:
            self.log.exception('error in daemon main loop')

        finally:
            try:
                self._stop_threads()
            except Exception as e:
                self.log.exception(e)

            self.log.debug("closing")
            self.server_closing_event.set()
            self.client.tasks.stop()
            self.log.debug("goodbye")

            if self.exit_event.is_set() and self.exit_command is not None:
                time.sleep(1)  # Maybe redundant
                self.log.debug("Executing %s" % self.exit_command)
                os.system(self.exit_command)

    def _stop_threads(self):
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._server_thread:
            self._server_thread.join()
            self._server_thread = None


    def poll(self, t):
        if (not self.last_check_time) or (self.poll_rate_seconds < t-self.last_check_time):
          self.sync_now(t)
          self.last_check_time = t #VP:maybe time.time() is better, include sync time?

    def sync_now(self, t=None):
        if t is None:
            t = time.time()
        try:
            self.client.sync()
        except ClientException:
            raise
        except Exception:
            self.log.exception('sync failed')

    def handle_client_command(self, client):
        """Read lines sent by client"""
        #client.setblocking(False)
        try:
            try:
                command = client.recv(128).rstrip('\n')
                if command:
                    reply = self.on_client_command(command)
                    if reply is not None:
                        client.sendall(reply.rstrip('\n') + '\n')
            except socket.error:
                pass

        finally:
            if client is not None:
                client.shutdown(socket.SHUT_RDWR)
                client.close()

    def on_client_command(self, command):
        if command == 'RESTART':
            self.log.info('restart requested')
            self.exit(' '.join(sys.argv))
            return "OK"

        elif command == 'STOP':
            self.log.info('stop requested')
            self.exit()
            return "OK"

        elif command == "SYNC":
            self.log.info('sync requested')
            try:
                self.sync_now()
            except Exception as e:
                return str(e)
            else:
                return "OK"

        elif command == "STATUS":
            self.log.info('status requested')
            return "running"

        return "BADCOMMAND"


# class FlushFile(file):
#     def write(self, data):
#         ret = super(FlushFile, self).write(data)
#         self.flush()
#         return ret


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

    def make_daemon(self, debug=None):
        conf_path = self.args.conf or constants.CONF_PATH
        conf_path = abspath(conf_path)

        conf = settings.read(conf_path)
        firmware_conf_path = conf.get('daemon', 'conf', conf_path)
        # It may not exist if there is no installed firmware
        if os.path.exists(firmware_conf_path):
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
            running, msg = self.comms.status()
            if not running:
                sys.stdout.write('not running\n')
            else:
                sys.stdout.write(msg + '\n')
            return 0

        #\TODO Get pid from config file....
        PIDFILE='/var/run/DP-d.pid'
        if os.path.exists(PIDFILE):
            sys.exit("pid file (%s) already exists" % PIDFILE)

        try:
            if args.foreground:
                dataplicity_daemon = self.make_daemon()
                dataplicity_daemon.start()
            else:
                #daemon_context = DaemonContext(pidfile=TimeoutPIDLockFile(PIDFILE, 1),stderr=sys.stderr) #logs forced to console.
                daemon_context = DaemonContext(pidfile=TimeoutPIDLockFile(PIDFILE, 1))
                with daemon_context:
                    dataplicity_daemon = self.make_daemon()
                    dataplicity_daemon.start()

        except Exception, e:
            from traceback import print_exc
            print_exc(e)
