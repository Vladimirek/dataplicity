from __future__ import unicode_literals


from dataplicity.app.subcommand import SubCommand
from dataplicity.client.serial import get_default_serial
from dataplicity import constants

import sys
import os
import os.path


conf_template = """
#----------------------------------------------------------------#
# Dataplicity Device Configuration                               #
# This information is used by the server to identify the device  #
#----------------------------------------------------------------#

[server]
# URL of the Dataplicity JSONRPC server
# Visit this URL for api documentation
url = {SERVER_URL}

[device]
# Name of the device to be displayed in the device tree
name = {name}

# The device class
class = {class}

# A unique serial number
serial = {serial}

# Auth token
auth = {auth_token}

# Text used to identify the device when using --auto
auto_device_text = {auto_device_text}

# Company subdomain or ID when using --auto
subdomain =  {subdomain}

# Directory where dataplicity will store 'live' settings which can be updated by the server
settings = {SETTINGS_PATH}

[daemon]
port = 3366
conf = {FIRMWARE_CONF_PATH}

[firmware]
path = {FIRMWARE_PATH}

"""


class Init(SubCommand):
    """Initialize this device for first use.

    There are two ways to initialize a device, either supply your username & password (with -u and -p), or use the --auto switch which authorizes the device after it has been confirmed via the web interface.

    Note: If you don't supply your username and password on the command line, you will be prompted to enter it interactively.

    """
    help = "Initialize this device for first use"

    def add_arguments(self, parser):
        parser.add_argument('--server', dest="server", metavar="SERVER URL", default=constants.SERVER_URL,
                            help="URL for Dataplicity api")
        parser.add_argument('--class', dest="cls", metavar="DEVICE CLASS", default=None,
                            help="the device class to use")
        parser.add_argument('-d', '--conf-dir', dest="output", metavar="PATH", default="/etc/dataplicity/",
                            help="location to write device configuration")
        parser.add_argument('--serial', dest="serial", metavar="SERIAL", default=None,
                            help="serial number for this device, omit to generate a serial number automatically")
        parser.add_argument('--name-prefix', dest="name_prefix", metavar="PREFIX", default='',
                            help="a string to prefix device name with")
        parser.add_argument('--name', dest="name", metavar="NAME", default=None,
                            help="a friendly name for this device")
        parser.add_argument('-f', '--force', default=False, action="store_true",
                            help="force overwrite of conf file if it exists")
        parser.add_argument('--dry', default=False, action="store_true",
                            help="don't write the conf file, just print it to stdout")
        parser.add_argument('-u', '--user', dest="user", metavar="USERNAME", default=None, required=False,
                            help="your dataplicity.com username")
        parser.add_argument('-p', '--password', dest="password", metavar="PASSWORD", default=None, required=False,
                            help="your dataplicity.com password")
        parser.add_argument('--auto', dest="auto", required=False, default='', metavar="TEXT TO IDENTIFY DEVICE",
                            help="auto-register online")
        parser.add_argument('--subdomain', dest="subdomain", required=False, default='', metavar="SUBDOMAIN",
                            help="Your company subdomain, if using --auto")

    def run(self):
        args = self.args
        auto = args.auto

        user = args.user
        if user is None and not auto:
            user = raw_input('username: ')

        password = args.password
        if password is None and not auto:
            import getpass
            password = getpass.getpass('password: ')

        if auto and not args.cls:
            sys.stderr.write('device class (--class) must be specified with --auto\n')
            return -1

        if auto and not args.subdomain:
            sys.stderr.write('subdomain (--subdomain) must be specified with --auto\n')
            return -1

        output_dir = args.output
        device_conf_path = os.path.join(output_dir, 'dataplicity.conf')

        serial = args.serial
        name = args.name
        if serial is None:
            serial = get_default_serial()
        if name is None:
            name = serial
        name = args.name_prefix + name

        from dataplicity import jsonrpc
        remote = jsonrpc.JSONRPC(args.server)

        sys.stdout.write('authenticating with server...\n')
        auto_device_subdomain = None
        if auto:
            auth_token = "file:/var/dataplicity/authtoken"
            auto_device_subdomain = args.subdomain
            # check if authtoken file already exists. If it does, delete it
            token_file = auth_token.split(':')[1]
            if os.path.exists(token_file):
                try:
                    os.remove(token_file)
                except Exception as e:
                    # Helpful errors FTW
                    sys.stderr.write("couldn't delete auth file\n")
                    sys.stderr.write("do you need to run this command with 'sudo'?\n")
                    return -1
        else:
            auth_token = remote.call('device.auth',
                                     serial=serial,
                                     username=user,
                                     password=password)
            sys.stdout.write('device authenticated\n')

        FIRMWARE_CONF_PATH = os.path.join(constants.FIRMWARE_PATH, 'current/dataplicity.conf')
        template_data = {"serial": serial,
                         "name": name,
                         "class": args.cls or 'default',
                         "auth_token": auth_token,
                         "auto_device_text": auto,
                         "subdomain": auto_device_subdomain or '',
                         "SERVER_URL": args.server or constants.SERVER_URL,
                         "SETTINGS_PATH": constants.SETTINGS_PATH,
                         "FIRMWARE_PATH": constants.FIRMWARE_PATH,
                         "FIRMWARE_CONF_PATH": FIRMWARE_CONF_PATH}
        conf_contents = conf_template.format(**template_data)

        if os.path.exists(device_conf_path) and not (args.force or args.dry):
            sys.stderr.write("a file called \"{}\" exists. Use --force to overwrite\n".format(device_conf_path))
            return -1

        if args.dry:
            sys.stdout.write(conf_contents.lstrip())
            return

        if not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir)
            except Exception as e:
                # Helpful errors FTW
                sys.stderr.write("couldn't create {} ({})\n".format(output_dir, e))
                sys.stderr.write("do you need to run this command with 'sudo'?\n")
                return -1

        def write_conf(path, contents):
            try:
                with open(path, 'wt') as f:
                    f.write(contents)
            except IOError as e:
                if e.errno == 13:
                    # Hold the user's hand
                    sys.stderr.write("No permission to write to {}\n".format(device_conf_path))
                    sys.stderr.write("You may need to run this with sudo\n")
                    raise SystemExit(1)
                raise
            sys.stdout.write("wrote {}\n".format(path))

        write_conf(device_conf_path, conf_contents)

        for path in (constants.SETTINGS_PATH, constants.FIRMWARE_PATH):
            if not os.path.exists(path):
                try:
                    os.makedirs(path)
                    os.chmod(path, 0777)
                except OSError:
                    sys.stderr.write('Unable to create directory {} ({})\n'.format(constants.SETTINGS_PATH, e))
                    return -1
                else:
                    sys.stdout.write("created {}\n".format(path))
