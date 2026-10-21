variable "region" { type = string }
variable "route_probe_key" {
  type      = string
  sensitive = true
  validation {
    condition     = can(regex("^[0-9a-f]{64}$", var.route_probe_key))
    error_message = "Generate an independent 32-byte hex route-probe key with prepare.py."
  }
}
variable "availability_zone" { type = string }
variable "image_id" { type = string }
variable "volume_type" { type = string }
variable "flavor_ids" { type = object({ app = string, db = string }) }
variable "external_network_name" {
  type = string

}
variable "name_prefix" {
  type = string

  validation {
    condition     = startswith(var.name_prefix, "otus-dz05")
    error_message = "Resources must use the DZ05 prefix."
  }
}
variable "subnet_cidr" {
  type = string

}
variable "admin_cidrs" {
  type = list(string)
  validation {
    condition     = length(var.admin_cidrs) > 0 && alltrue([for c in var.admin_cidrs : can(cidrnetmask(c)) && split("/", c)[1] == "32"])
    error_message = "Provide operator IPv4 /32 addresses."
  }
}
variable "ssh_public_key_path" {
  type = string

}
variable "boot_disk_gb" {
  type = number

}
variable "admin_ssh_port" {
  type = number

  validation {
    condition     = var.admin_ssh_port == 22
    error_message = "SSH must use standard TCP/22; diagnose the network route instead of changing the port."
  }
}

variable "nodes" {
  type = map(object({ host = number, role = string }))
  validation {
    condition = length(var.nodes) >= 4 && length(var.nodes) <= 10 && contains(keys(var.nodes), "app") && alltrue([
      for name, node in var.nodes : can(regex("^(app|db[1-9])$", name)) && node.role == (name == "app" ? "app" : "db")
    ]) && length(distinct([for node in values(var.nodes) : node.host])) == length(var.nodes)
    error_message = "Use one app and 3 to 9 uniquely addressed database nodes named db1 through db9."
  }
}
variable "dns_nameservers" { type = list(string) }
