variable "repository_name" {
  type        = string
  description = "ECR repository name"
}

variable "region" {
  type        = string
  description = "AWS region of the ECR repository"
}

variable "source_dir" {
  type        = string
  description = "Docker build context (also hashed to derive the image tag)"
}

variable "dockerfile" {
  type        = string
  description = "Dockerfile path relative to source_dir"
  default     = "Dockerfile"
}

variable "target" {
  type        = string
  description = "Multi-stage build target"
}

variable "platform" {
  type        = string
  description = "Image platform. AgentCore Runtime requires linux/arm64."
  default     = "linux/arm64"

  validation {
    condition     = contains(["linux/arm64", "linux/amd64"], var.platform)
    error_message = "platform must be linux/arm64 or linux/amd64."
  }
}

variable "image_tag" {
  type        = string
  description = "Use a pre-built tag (e.g. from CI) instead of the source hash"
  default     = ""
}

variable "skip_build" {
  type        = bool
  description = "Skip the local docker build (image already pushed by CI)"
  default     = false
}

variable "images_to_keep" {
  type    = number
  default = 10
}

variable "force_delete" {
  type        = bool
  description = "Allow terraform destroy to delete the repository with images in it"
  default     = true
}
