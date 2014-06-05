from dataplicity.app.subcommand import SubCommand
from dataplicity.client import Client

import io
from os.path import join, dirname, normpath

import logging
log = logging.getLogger('dataplicity')


class Register(SubCommand):
    """Register this device with the Dataplicity server"""
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument('--auth', dest="auth", metavar="AUTH TOKEN", default=None, required=False,
                            help="Authorization token (the default use the auth token in datpalicity.conf)")

    def run(self):
        args = self.args

        client = self.app.make_client(log)
        remote = client.remote
        conf = client.conf

        log.debug("rpc url is {}".format(remote.url))

        device_class_name = conf.get('device', 'class')
        serial = conf.get('device', 'serial')
        name = conf.get('device', 'name', 'serial')
        auth_token = conf.get('device', 'auth', args.auth)

        path = conf.get('device', 'path', None)
        if path is None:
            path = "{}.{}".format(device_class_name, name)

        ui_path = conf.get('register', 'ui', None)
        if ui_path is not None:
            ui_path = normpath(join(dirname(conf.path), ui_path))
            try:
                with io.open(ui_path, 'rt') as f:
                    ui = f.read()
            except IOError:
                error_msg = 'UI file "{}" could not be read'.format(ui_path)
                log.exception(error_msg)
                print error_msg
                return -1
        else:
            # No initial UI xml suplied. Fine, not an error
            ui = None

        print "Registering device..."
        result = remote.call("device.register",
                             auth_token=auth_token,
                             name=name,
                             serial=serial,
                             device_class_name=device_class_name,
                             ui=ui,
                             path=path)
        print result["message"]

        samplers = client.samplers.enumerate_samplers()
        if samplers:
            with remote.batch() as batch:
                batch.call_with_id('auth_result',
                                   'device.check_auth',
                                   device_class=client.device_class,
                                   serial=client.serial,
                                   auth_token=client.auth_token)
                batch.call_with_id("create_samplers_result",
                                   "device.create_samplers",
                                   sampler_names=samplers)
            if not batch.get_result('auth_result'):
                print "Unable to authenticate with the Dataplicity server, check username and password"
                return -1
            batch.get_result('create_samplers_result')

        with remote.batch() as batch:
            batch.call_with_id('auth_result',
                               'device.check_auth',
                               device_class=client.device_class,
                               serial=client.serial,
                               auth_token=client.auth_token)
            batch.call_with_id('url_result',
                               'device.get_manage_url')
        url = batch.get_result('url_result')

        print "Run 'dataplicity manage' or visit {} to manage your device".format(url)
