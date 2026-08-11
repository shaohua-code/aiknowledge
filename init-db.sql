-- PostgreSQL 初始化脚本
-- 由 docker-entrypoint-initdb.d 在首次启动时自动执行
-- 注意：此脚本以超级用户（POSTGRES_USER）身份运行

-- 安装扩展到 knowledge_hub 库
-- pgvector 官方镜像已内置 vector 扩展文件，此处只需 CREATE EXTENSION 激活
CREATE EXTENSION IF NOT EXISTS vector;

-- pgcrypto：UUID 生成（gen_random_uuid），PostgreSQL 自带
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- citext：大小写不敏感文本类型，用于 API Key 等字段
CREATE EXTENSION IF NOT EXISTS citext;

-- 授权：让 aiknowledge 用户能使用这些扩展
GRANT USAGE ON SCHEMA public TO aiknowledge;
GRANT CREATE ON SCHEMA public TO aiknowledge;

-- 输出已安装扩展，便于启动日志确认
DO $$
BEGIN
    RAISE NOTICE '已安装扩展: %',
        (SELECT string_agg(extname, ', ') FROM pg_extension WHERE extname IN ('vector', 'pgcrypto', 'citext'));
END $$;
