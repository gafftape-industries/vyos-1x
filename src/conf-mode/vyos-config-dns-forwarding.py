#!/usr/bin/env python3
#
# Copyright (C) 2018 VyOS maintainers and contributors
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 or later as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#

import sys
import os

import netifaces
import jinja2

from vyos.config import Config
from vyos.util import ConfigError

config_file = r'/etc/powerdns/recursor.conf'

# XXX: pdns recursor doesn't like whitespace near entry separators,
# especially in the semicolon-separated lists of name servers.
# Please be careful if you edit the template.
config_tmpl = """
### Autogenerated by vyos-config-dns-forwarding.py ###

# Non-configurable defaults
daemon=yes
threads=1
allow-from=0.0.0.0/0
log-common-errors=yes

# cache-size
max-cache-entries={{ cache_size }}

# ignore-hosts-file
export-etc-hosts={{ export_hosts_file }}

# listen-on
local-address={{ listen_on | join(',') }}

# domain ... server ...
{% if domains -%}

{% for d in domains -%}
forward-zones={{ d.name }}={{ d.servers | join(";") }}
{% endfor -%}

{% endif %}

# name-server
forward-zones-recurse=.={{ name_servers | join(';') }}

"""

default_config_data = {
    'cache_size' : 10000,
    'export_hosts_file': 'yes',
    'listen_on': [],
    'interfaces': [],
    'name_servers': [],
    'domains': []
}


# borrowed from: https://github.com/donjajo/py-world/blob/master/resolvconfReader.py, THX!
def get_resolvers(file):
    resolvers = []
    try:
        with open(file, 'r') as resolvconf:
            for line in resolvconf.readlines():
                line = line.split('#',1)[0];
                line = line.rstrip();
                if 'nameserver' in line:
                    resolvers.append(line.split()[1])
        return resolvers
    except IOError:
        return []

def get_config():
    dns = default_config_data
    conf = Config()
    if not conf.exists('service dns forwarding'):
        return None
    else:
        conf.set_level('service dns forwarding')

    if conf.exists('cache-size'):
        cache_size = conf.return_value('cache-size')
        dns['cache_size'] = cache_size

    if conf.exists('domain'):
        for node in conf.list_nodes('domain'):
            server = conf.return_values("domain {0} server".format(node))
            domain = {
                "name": node,
                "servers": server
            }
            dns['domains'].append(domain)

    if conf.exists('ignore-hosts-file'):
        dns.setdefault('export_hosts_file', "no")

    if conf.exists('name-server'):
        name_servers = conf.return_values('name-server')
        dns['name_servers'] = dns['name_servers'] + name_servers

    if conf.exists('system'):
        conf.set_level('system')
        system_name_servers = []
        system_name_servers = conf.return_values('name-server')
        if not system_name_servers:
            print("DNS forwarding warning: No name-servers set under 'system name-server'\n")
        else:
            dns['name_servers'] = dns['name_servers'] + system_name_servers
        conf.set_level('service dns forwarding')

    ## Hacks and tricks

    # The old VyOS syntax that comes from dnsmasq was "listen-on $interface".
    # pdns wants addresses instead, so we emulate it by looking up all addresses
    # of a given interface and writing them to the config
    if conf.exists('listen-on'):
        interfaces = conf.return_values('listen-on')

        listen4 = []
        listen6 = []
        for interface in interfaces:
            addrs = netifaces.ifaddresses(interface)
            for ip4 in addrs[netifaces.AF_INET]:
                listen4.append(ip4['addr'])

            for ip6 in addrs[netifaces.AF_INET6]:
                listen6.append(ip6['addr'])

        dns['listen_on'] = listen4 + listen6

        # Save interfaces in the dict for the reference
        dns['interfaces'] = interfaces

    # Add name servers received from DHCP
    if conf.exists('dhcp'):
        interfaces = []
        interfaces = conf.return_values('dhcp')
        for interface in interfaces:
            dhcp_resolvers = get_resolvers("/etc/resolv.conf.dhclient-new-{0}".format(interface))
            if dhcp_resolvers:
                dns['name_servers'] = dns['name_servers'] + dhcp_resolvers

    return dns

def verify(dns):
    # bail out early - looks like removal from running config
    if dns is None:
        return None

    if not dns['interfaces']:
        raise ConfigError('Error: DNS forwarding requires a configured listen interface!')

    for interface in dns['interfaces']:
        try:
            netifaces.ifaddresses(interface)[netifaces.AF_INET]
        except KeyError as e:
            raise ConfigError('Error: Interface {0} has no IP address assigned'.format(interface))

    if dns['domains']:
        for domain in dns['domains']:
            if not domain['servers']:
                raise ConfigError('Error: No server configured for domain {0}'.format(domain['name']))

    return None

def generate(dns):
    # bail out early - looks like removal from running config
    if dns is None:
        return None

    tmpl = jinja2.Template(config_tmpl)

    config_text = tmpl.render(dns)
    with open(config_file, 'w') as f:
        f.write(config_text)
    return None

def apply(dns):
    if dns is not None:
        os.system("systemctl restart pdns-recursor")
    else:
        # DNS forwarding is removed in the commit
        os.system("systemctl stop pdns-recursor")
        os.unlink(config_file)

    return None

if __name__ == '__main__':
    try:
        c = get_config()
        verify(c)
        generate(c)
        apply(c)
    except ConfigError as e:
        print(e)
        sys.exit(1)
