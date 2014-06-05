from dataplicity.app.subcommands.daemon import Daemon
from dataplicity.app.subcommand import SubCommand


class Run(SubCommand):
    """Run the dataplicity service in the foreground"""
    help = """Run a dataplicity service in the foreground (useful for debugging)"""

    def add_arguments(self, parser):
        return parser

    def make_daemon(self, debug=True):
        if debug is None:
            debug = self.args.debug
        self.app.init_logging()
        dataplicity_daemon = Daemon(self.app.conf,
                                    foreground=True,
                                    debug=debug)
        return dataplicity_daemon

    def run(self):
        dataplicity_daemon = self.make_daemon()
        #dataplicity_daemon.sync_now()
        dataplicity_daemon.start()
