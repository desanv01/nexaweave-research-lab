"""\n配置管理\n统一从项目根目录的 .env 文件加载配置\n"""

import os
from dotenv import load_dotenv

# 加载项目根目录的 .env 文件
# 路径: NexaWeave/.env (相对于 backend/app/config.py)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    # 如果根目录没有 .env，尝试加载环境变量（用于生产环境）
    load_dotenv(override=True)


class Config:
    """Flask配置类"""
    NEXAWEAVE_APP_MODE = 'legacy'
    MIROFISH_APP_MODE = NEXAWEAVE_APP_MODE  # Config-class compatibility alias.
    
    # Flask配置
    SECRET_KEY = os.environ.get('SECRET_KEY', 'nexaweave-secret-key')
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    # Explicit browser Origin policy. The factory reads the environment at
    # startup unless a subclass overrides this attribute.
    NEXAWEAVE_ALLOWED_ORIGINS = ('http://localhost:3000', 'http://127.0.0.1:3000')
    MIROFISH_ALLOWED_ORIGINS = NEXAWEAVE_ALLOWED_ORIGINS  # Config-class compatibility alias.
    
    # JSON配置 - 禁用ASCII转义，让中文直接显示
    JSON_AS_ASCII = False
    
    # LLM配置（统一使用OpenAI格式）
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'gpt-4o-mini')
    
    # Zep配置
    ZEP_API_KEY = os.environ.get('ZEP_API_KEY')
    
    # 文件上传配置
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown', 'docx'}
    
    # 文本处理配置
    DEFAULT_CHUNK_SIZE = 500  # 默认切块大小
    DEFAULT_CHUNK_OVERLAP = 50  # 默认重叠大小
    
    # OASIS模拟配置
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/simulations')
    
    # OASIS平台可用动作配置
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]
    
    # Report Agent配置
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))
    
    @classmethod
    def validate(cls) -> list[str]:
        """验证必要配置"""
        errors: list[str] = []
        if not cls.LLM_API_KEY:
            errors.append("LLM_API_KEY 未配置")
        if not cls.ZEP_API_KEY:
            errors.append("ZEP_API_KEY 未配置")
        if os.environ.get("ZEP_API_URL"):
            errors.append("ZEP_API_URL 不受支持；NexaWeave 仅连接 Zep Cloud")
        if cls.DEBUG:
            import warnings
            warnings.warn("Flask DEBUG mode is enabled. Do not use in production.", RuntimeWarning)
        return errors

    @classmethod
    def validate_readonly(cls) -> list[str]:
        """Validate the separate graph-read host without legacy model/Zep keys."""
        try:
            from .services.knowledge_read_facade import ReadHostSettings
            ReadHostSettings.from_config(cls)
            if cls.DEBUG or os.environ.get('FLASK_HOST', '127.0.0.1') not in {'127.0.0.1', '::1', 'localhost'}:
                raise ValueError
        except (ValueError, OSError):
            return ['Invalid graph-read configuration']
        return []
