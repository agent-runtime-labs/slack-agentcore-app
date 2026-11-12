################################################################################
# draw_diagram renderer: Mermaid -> PNG, in our own account
#
# A container-image Lambda (Node + Chromium, see backends/diagram_renderer) that only the
# agent runtime may invoke. It has no secrets, tables or queues: its role can write its
# own logs and nothing else, and it never logs the diagram source. Off unless
# diagram_renderer_enabled, which also leaves draw_diagram out of the agent.
################################################################################

module "diagram_renderer_image" {
  count  = var.diagram_renderer_enabled ? 1 : 0
  source = "../tf-modules/aws/container-image"

  repository_name = "${local.name_prefix}-diagram-renderer"
  region          = var.region
  source_dir      = "${local.backends_dir}/diagram_renderer"
  target          = "lambda"
}

module "diagram_renderer_fn" {
  count  = var.diagram_renderer_enabled ? 1 : 0
  source = "../tf-modules/aws/lambda-function"

  name        = "${local.name_prefix}-diagram-renderer"
  description = "Renders the agent's Mermaid diagrams to PNG with headless Chromium"
  image_uri   = module.diagram_renderer_image[0].image_uri
  command     = ["index.handler"]
  # Chromium needs the memory (and the CPU that comes with it); a cold start adds a few seconds.
  timeout     = 30
  memory_size = 2048

  # No policy_json: nothing beyond logging.
}

locals {
  diagram_renderer_function_name = var.diagram_renderer_enabled ? module.diagram_renderer_fn[0].function_name : ""

  diagram_agent_env = { DIAGRAM_RENDER_FUNCTION = local.diagram_renderer_function_name }

  diagram_agent_statements = var.diagram_renderer_enabled ? [{
    Sid      = "InvokeDiagramRenderer"
    Effect   = "Allow"
    Action   = ["lambda:InvokeFunction"]
    Resource = module.diagram_renderer_fn[0].function_arn
  }] : []
}
