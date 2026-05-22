# adopt the existing parsely-api repo into Tofu's state instead of recreating it
import {
  to = aws_ecr_repository.parsely_ecr_repository
  id = "parsely-api"
}

# create ECR repository
resource "aws_ecr_repository" "parsely_ecr_repository" {
  name       = "parsely-api"
  image_tag_mutability = "MUTABLE"

  encryption_configuration {
    encryption_type = "AES256"
  }
 
}

# (optional) create lifecycle policy
# resource "aws_ecr_lifecycle_policy" "parsely_ecr_repository" {
#     repository = aws_ecr_repository.parsely_ecr_repository.name

#     policy = jsonencode({
#         rules = [
#             {

#             }
#         ]
#     })
# }