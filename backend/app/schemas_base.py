"""모든 API 입력·출력 모델이 물려받는 pydantic 기본 클래스.
파이썬 snake_case 필드를 JSON camelCase로 바꾸는 설정을 한곳에 둔다."""
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    """snake_case 필드를 camelCase 별칭으로도 받는 기본 모델.
    응답·느슨한 입력처럼 모르는 필드를 무시해도 되는 곳에 쓴다."""
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class StrictApiModel(ApiModel):
    """ApiModel과 같되 모르는 필드가 오면 422로 거부한다(설정은 부모와 합쳐진다).
    오타·조작된 필드를 조용히 버리면 안 되는 입력 모델에 쓴다."""
    model_config = ConfigDict(extra="forbid")
