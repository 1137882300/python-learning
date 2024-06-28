# AI 知识点

## chain_type

> 设置 chain_type 可以防止 token 过长

- "stuff": 整个文档一股脑的交给llm
- "map_reduce": 文档先切块，每块单独交给llm
- "refine": 文档先切块，一块交给llm，得到结果再和下一块一起交给llm，循环这个过程
- "map_rerank": 文档先切块，并给每一块打分，分值最好的交给llm