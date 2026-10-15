from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelConfig:
    name: str
    context_length: int


class ProviderConfig:
    name: str
    priority: int
    api_base: str
    models: list[dict[str, Any]]
    env_key: str


class LLMConfig:
    providers: dict[str, ProviderConfig]
    default: dict[str, Any]


class ToolInputConfig:
    name: str
    type: str
    required: bool = True


class ToolOutputConfig:
    name: str
    type: str


class ToolParameterConfig:
    type: str
    default: Any
    min: float | None = None
    max: float | None = None
    values: list[str] | None = None


class ToolConfig:
    id: str
    name: str
    category: str
    executable: str
    docker_image: str | None = None
    inputs: list[dict[str, Any]]
    outputs: list[dict[str, Any]]
    parameters: dict[str, ToolParameterConfig]
    timeout: int = 3600


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    zhipuai_api_key: str = ""
    moonshot_api_key: str = ""
    minimax_api_key: str = ""
    deepseek_api_key: str = ""
    new_api_key: str = ""
    new_api_base_url: str = ""
    new_api_model: str = ""
    dashscope_api_key: str = ""
    openai_api_key: str = ""  # Set via .env or OPENAI_API_KEY/openai_api_key environment variable.
    openai_base_url: str = "https://coding.dashscope.aliyuncs.com/v1"  # 阿里云百炼Coding接口

    database_url: str = "postgresql://user:password@localhost:5432/genefamily"
    redis_url: str = "redis://localhost:6379/0"

    vector_db_type: str = "qdrant"
    qdrant_url: str = "http://localhost:6333"
    milvus_host: str = "localhost"
    milvus_port: int = 19530

    log_level: str = "INFO"
    environment: str = "development"

    @property
    def llm_config_path(self) -> Path:
        return Path("config/llm.yaml")

    @property
    def tools_config_path(self) -> Path:
        return Path("config/tools.yaml")


class ConfigLoader:
    _instance = None
    _settings: Settings | None = None
    _llm_config: dict | None = None
    _tools_config: dict | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = Settings()
        return self._settings

    def load_llm_config(self) -> dict:
        if self._llm_config is None:
            config_path = self.settings.llm_config_path
            if config_path.exists():
                with open(config_path) as f:
                    self._llm_config = yaml.safe_load(f)
            else:
                self._llm_config = {}
            if self._llm_config is None:
                self._llm_config = {}
            
            # 注入 LLM API 配置。provider-specific key 优先用于对应 provider。
            self._llm_config["openai_api_key"] = self.settings.openai_api_key
            self._llm_config["openai_base_url"] = self.settings.openai_base_url
            self._llm_config["zhipuai_api_key"] = self.settings.zhipuai_api_key
            self._llm_config["moonshot_api_key"] = self.settings.moonshot_api_key
            self._llm_config["minimax_api_key"] = self.settings.minimax_api_key
            use_new_api_bundle = bool(
                not self.settings.deepseek_api_key
                and self.settings.new_api_key
                and self.settings.new_api_base_url
                and self.settings.new_api_model
            )
            self._llm_config["deepseek_api_key"] = (
                self.settings.new_api_key
                if use_new_api_bundle
                else self.settings.deepseek_api_key
            )
            self._llm_config["dashscope_api_key"] = self.settings.dashscope_api_key

            if use_new_api_bundle:
                providers = self._llm_config.setdefault("providers", {})
                providers.setdefault("deepseek", {})["api_base"] = (
                    self.settings.new_api_base_url
                )
            
            # 设置默认模型（如果llm.yaml中没有）
            if "default" not in self._llm_config:
                self._llm_config["default"] = {}
            default_config = self._llm_config["default"]
            if use_new_api_bundle:
                default_config["model"] = self.settings.new_api_model
            if not default_config.get("provider"):
                default_config["provider"] = "deepseek"
            if not default_config.get("model"):
                default_config["model"] = "deepseek-v4-flash"
            if not default_config.get("temperature"):
                default_config["temperature"] = 0.7
            if not default_config.get("max_tokens"):
                default_config["max_tokens"] = 4096
        return self._llm_config

    def load_tools_config(self) -> dict:
        if self._tools_config is None:
            config_path = self.settings.tools_config_path
            if config_path.exists():
                with open(config_path) as f:
                    self._tools_config = yaml.safe_load(f)
            else:
                self._tools_config = {}
            if self._tools_config is None:
                self._tools_config = {}
        return self._tools_config

    def get_provider_config(self, provider: str) -> dict | None:
        llm_config = self.load_llm_config()
        return llm_config.get("providers", {}).get(provider)

    def get_tool_config(self, tool_id: str) -> dict | None:
        tools_config = self.load_tools_config()
        return tools_config.get("tools", {}).get(tool_id)

    def get_default_provider(self) -> str:
        llm_config = self.load_llm_config()
        return llm_config.get("default", {}).get("provider", "glm")

    def get_default_model(self) -> str:
        llm_config = self.load_llm_config()
        return llm_config.get("default", {}).get("model", "glm-5")


config = ConfigLoader()
