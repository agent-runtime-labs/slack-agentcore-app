# ============================================================================
# ECR repository + docker buildx build/push, re-run only when sources change.
# ============================================================================

locals {
  # Hash every file that ends up in the image (tests and caches excluded).
  src_files = sort([
    for f in fileset(var.source_dir, "**") : f
    if !can(regex("(^|/)(tests|__pycache__|\\.venv|\\.pytest_cache)(/|$)", f))
  ])
  src_hash  = substr(sha1(join("", [for f in local.src_files : filesha1("${var.source_dir}/${f}")])), 0, 12)
  image_tag = var.image_tag != "" ? var.image_tag : "${var.target}-${local.src_hash}"
  image_uri = "${aws_ecr_repository.this.repository_url}:${local.image_tag}"
}

resource "aws_ecr_repository" "this" {
  name                 = var.repository_name
  image_tag_mutability = "MUTABLE"
  force_delete         = var.force_delete

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  repository = aws_ecr_repository.this.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last ${var.images_to_keep} images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = var.images_to_keep
      }
      action = { type = "expire" }
    }]
  })
}

resource "terraform_data" "build_and_push" {
  count = var.skip_build ? 0 : 1

  triggers_replace = [local.image_uri]

  provisioner "local-exec" {
    command     = "${path.module}/build-and-push.sh"
    interpreter = ["/bin/bash", "-c"]
    environment = {
      AWS_REGION  = var.region
      IMAGE_URI   = local.image_uri
      CONTEXT_DIR = abspath(var.source_dir)
      DOCKERFILE  = abspath("${var.source_dir}/${var.dockerfile}")
      TARGET      = var.target
      PLATFORM    = var.platform
    }
  }
}
