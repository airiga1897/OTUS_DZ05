"""Filters without third-party collections."""
import ipaddress


class FilterModule:
    def filters(self):
        return {'dz05_mysql_network': lambda value: ipaddress.IPv4Network(value, strict=True).with_netmask}
