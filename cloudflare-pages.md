# Cloudflare Pages 部署配置

在 Cloudflare Dashboard → Workers & Pages → Create → Pages → Connect to Git 选本仓库，
构建配置填：

| 项 | 值 |
|---|---|
| Framework preset | None |
| Build command | `python3 scripts/gen_mock.py` |
| Build output directory | `site` |
| Root directory | `/` |

数据不入库，由构建命令现场生成到 `site/data/`，随站点一起发布。
换一组演示数据只需改 `--seed`，重新部署即可。
