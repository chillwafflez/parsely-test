# create ECR repository
resource "aws_ecr_repository" "parsely_ecr_repository" {
  name       = "parsely-api"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

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