from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    # Emits camelCase JSON for the frontend wire format while accepting both
    # casings on input. All Pydantic wire types in this project extend this.
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )
