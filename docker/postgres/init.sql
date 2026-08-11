-- 智能知识中台数据库初始化
-- 启用所需扩展

-- pgvector：向量存储与相似度检索
CREATE EXTENSION IF NOT EXISTS vector;

-- pgcrypto：提供 gen_random_uuid() 等加密函数
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- citext：大小写不敏感文本类型（项目编码、知识库 code 等使用）
CREATE EXTENSION IF NOT EXISTS citext;

-- 输出确认信息
DO $$
BEGIN
    RAISE NOTICE '数据库扩展初始化完成：vector / pgcrypto / citext';
END $$;
