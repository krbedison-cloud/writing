from typing import Literal
from .output_validation import number, text_list, dimensions
from pydantic import BaseModel, Field, ConfigDict, model_validator, field_validator, AliasChoices

KINDS = {"summary": ("概括 / 短简报", 20), "practical": ("应用文 / 短评", 30), "essay": ("大作文", 50)}
DIMENSIONS = ["审题立意", "要点提炼", "材料运用", "结构逻辑", "语言表达", "文体规范"]

class ProviderInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    protocol: Literal["openai", "anthropic", "gemini"] = "openai"
    base_url: str = Field(min_length=8, max_length=500)
    model: str = Field(min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9._:/@+\-]+$")
    api_key: str = Field(default="", max_length=2000)
    max_tokens: int = Field(default=6000, ge=1000, le=32000)
    timeout_seconds: int = Field(default=600, ge=60, le=1800)
    @model_validator(mode="after")
    def validate_url(self):
        from urllib.parse import urlsplit
        p = urlsplit(self.base_url)
        if p.scheme not in ("https", "http") or not p.hostname or p.username or p.password or p.query or p.fragment:
            raise ValueError("接口地址只能使用无凭据、查询参数的 HTTP(S) URL")
        self.base_url = self.base_url.rstrip("/")
        return self

class QuestionInput(BaseModel):
    number: int = Field(ge=1, le=100)
    kind: Literal["summary", "practical", "essay"]
    prompt: str = Field(min_length=4, max_length=16000)
    word_min: int = Field(default=0, ge=0, le=5000)
    word_max: int = Field(default=0, ge=0, le=5000)
    paper_rows: int = Field(default=0, ge=0, le=400)
    classification_note: str = Field(default="人工校对", max_length=600)
    material_mode: Literal["auto", "selected", "all", "custom"] = "auto"
    material_ids: list[int] = Field(default_factory=list, max_length=100)
    custom_materials: str = Field(default="", max_length=160000)
    @model_validator(mode="after")
    def limits(self):
        if any(not 1 <= id <= 999 for id in self.material_ids):
            raise ValueError("资料编号必须在 1–999 之间")
        if self.material_mode == 'selected' and not self.material_ids:
            raise ValueError("请至少选择一段资料")
        if self.material_mode == 'custom' and not self.custom_materials.strip():
            raise ValueError("人工材料不能为空")
        if self.word_max and self.word_min > self.word_max:
            raise ValueError("最少字数不能超过最多字数")
        return self

class PaperInput(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    year: str = Field(default="待确认", max_length=20)
    province: str = Field(default="待确认", max_length=40)
    volume: str = Field(default="待确认", max_length=40)
    materials: str = Field(max_length=160000)
    questions: list[QuestionInput] = Field(min_length=1, max_length=30)
    confirmed: bool = False
    @model_validator(mode="after")
    def unique_numbers(self):
        if len({q.number for q in self.questions}) != len(self.questions):
            raise ValueError("题号不能重复")
        return self

class AttemptInput(BaseModel):
    question_id: str
    answer: str = Field(min_length=1, max_length=20000)
    provider_ids: list[str] = Field(min_length=1, max_length=12)
    master_id: str

class RetryInput(BaseModel):
    provider_id: str | None = None

class ReferenceSelfCheck(BaseModel):
    word_count: int = Field(ge=0, le=20000)
    used_rows: int = Field(ge=0, le=20000, validation_alias=AliasChoices('used_rows','rows_used'))
    complies: bool
    format_note: str = Field(default="", max_length=1800)

class ReferenceDraft(BaseModel):
    high_score_answer: str = Field(min_length=10, max_length=12000)
    high_score_self_check: ReferenceSelfCheck | None = None
    @field_validator('high_score_self_check', mode='before')
    @classmethod
    def valid_self_check(cls, value):
        if value is None:
            return None
        try:
            return ReferenceSelfCheck.model_validate(value)
        except (ValueError, TypeError):
            return None

class RegenerateInput(BaseModel):
    provider_id: str
    direction: Literal["more", "less"]
    reference_id: str = "original"

class HighlightInput(BaseModel):
    signature: str = Field(pattern=r"^[a-f0-9]{64}$")
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=160000)
    color: str = Field(default="#ffff00", pattern=r"^#[0-9a-fA-F]{6}$")

class HighlightColor(BaseModel):
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")

class TaskAnalysis(BaseModel):
    @field_validator('requirements','material_basis',mode='before')
    @classmethod
    def list_text(cls,value):return text_list(value)

    task: str = Field(min_length=1, max_length=1000)
    genre: str = Field(min_length=1, max_length=200)
    requirements: list[str] = Field(min_length=1, max_length=12)
    material_basis: list[str] = Field(min_length=1, max_length=12)

class Grade(ReferenceDraft):
    task_analysis: TaskAnalysis | None = None
    score: float = Field(ge=0, le=50, allow_inf_nan=False)
    dimensions: dict[str, float]
    strengths: list[str] = Field(max_length=10)
    weaknesses: list[str] = Field(max_length=10)
    suggestions: list[str] = Field(max_length=10)
    evidence: list[str] = Field(min_length=1, max_length=12)
    high_score_answer: str = Field(min_length=10, max_length=12000)
    high_score_self_check: ReferenceSelfCheck | None = None
    @field_validator('task_analysis',mode='before')
    @classmethod
    def optional_analysis(cls,value):
        if value is None:return None
        try:return TaskAnalysis.model_validate(value)
        except (ValueError,TypeError):return None
    @field_validator('score',mode='before')
    @classmethod
    def numeric_score(cls,value):return number(value)
    @field_validator('strengths','weaknesses','suggestions','evidence',mode='before')
    @classmethod
    def list_text(cls,value):return text_list(value)
    @field_validator('dimensions',mode='before')
    @classmethod
    def dimension_shape(cls,value):return dimensions(value)
    @field_validator('dimensions')
    @classmethod
    def dimensions_valid(cls,value):
        import math
        if set(value)!=set(DIMENSIONS) or any(not math.isfinite(v) or not 0<=v<=100 for v in value.values()):
            raise ValueError('必须返回六项0–100维度分')
        return value

class Synthesis(BaseModel):
    @field_validator('consensus','disagreements','action_plan',mode='before')
    @classmethod
    def list_text(cls,value):return text_list(value)

    analysis: str = Field(min_length=10, max_length=10000)
    consensus: list[str] = Field(max_length=10)
    disagreements: list[str] = Field(max_length=10)
    action_plan: list[str] = Field(min_length=1, max_length=8)

class Portrait(BaseModel):
    @field_validator('strengths','weaknesses','action_plan',mode='before')
    @classmethod
    def list_text(cls,value):return text_list(value)

    analysis: str = Field(min_length=10, max_length=12000)
    strengths: list[str] = Field(min_length=1, max_length=8)
    weaknesses: list[str] = Field(min_length=1, max_length=8)
    action_plan: list[str] = Field(min_length=1, max_length=8)
