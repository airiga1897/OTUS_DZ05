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
  type    = string
  default = "external-network"
}
variable "name_prefix" {
  type    = string
  default = "otus-dz05"
  validation {
    condition     = startswith(var.name_prefix, "otus-dz05")
    error_message = "Resources must use the DZ05 prefix."
  }
}
variable "subnet_cidr" {
  type    = string
  default = "10.55.0.0/24"
}
variable "admin_cidrs" {
  type = list(string)
  validation {
    condition     = length(var.admin_cidrs) > 0 && alltrue([for c in var.admin_cidrs : can(cidrnetmask(c)) && split("/", c)[1] == "32"])
    error_message = "Provide operator IPv4 /32 addresses."
  }
}
variable "ssh_public_key_path" {
  type    = string
  default = "../.local/otus_dz05.pub"
}
variable "boot_disk_gb" {
  type    = number
  default = 20
}
variable "admin_ssh_port" {
  type    = number
  default = 443
  validation {
    condition     = contains([22, 443], var.admin_ssh_port)
    error_message = "External SSH must use 22 or 443."
  }
}
