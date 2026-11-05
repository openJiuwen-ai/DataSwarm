#!/bin/bash
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

set -e

DATASWARM_ROOT="${DATASWARM_ROOT:-$PWD}"   # 主仓 checkout 根（流水线 cwd）
BUILD_DIR="$DATASWARM_ROOT/build"          # 组合树——被测代码在这里
TEST_DIR="tests/unit_tests"
OUT_DIR="$DATASWARM_ROOT/outputs"
REPORT_HTML="$OUT_DIR/unit_test_report.html"
REPORT_XML="$OUT_DIR/unit_test_result.xml"
RESULT_JSON="$DATASWARM_ROOT/ut_result.json"
ut_result_value="PASSED"
UV_VENV_DIR="$BUILD_DIR/.venv"
TUNA="https://pypi.tuna.tsinghua.edu.cn/simple"
HW="https://repo.huaweicloud.com/repository/pypi/simple"

# ---------- 环境自检（CI 排障第一现场：先看清检出的是什么、站在哪） ----------
echo "===== 环境自检 ====="
echo "[env] PWD=$(pwd)"
echo "[env] DATASWARM_ROOT=${DATASWARM_ROOT}"
echo "[env] 平台=$(uname -s)  uv=$(uv --version 2>/dev/null || echo '未安装')  python3=$(python3 --version 2>&1)"
if ( cd "$DATASWARM_ROOT" 2>/dev/null ); then
    echo "[env] 主仓 HEAD=$(git -C "$DATASWARM_ROOT" rev-parse --short HEAD 2>/dev/null || echo 未知) 分支=$(git -C "$DATASWARM_ROOT" branch --show-current 2>/dev/null || echo detached)"
    echo "[env] scripts/dataswarm/：$([ -d "$DATASWARM_ROOT/scripts/dataswarm" ] && echo 存在 || echo ❌缺失)  scripts/ci/run_ut_ci.sh：$([ -f "$DATASWARM_ROOT/scripts/ci/run_ut_ci.sh" ] && echo 存在 || echo ❌缺失)"
else
    echo "[env] ❌ DATASWARM_ROOT 不可进入——流水线 cwd 是否为主仓 checkout？"
fi

# ---------- obsutil（报告上传，沿用旧脚本） ----------
if ! command -v obsutil &> /dev/null; then
    wget "https://obs-community.obs.cn-north-1.myhuaweicloud.com/obsutil/current/obsutil_linux_amd64.tar.gz" -O obsutil.tar.gz
    tar -zxf obsutil.tar.gz
    cd obsutil_* || { echo "解压失败"; exit 1; }
    mkdir -p ~/.local/bin/
    cp obsutil ~/.local/bin/
    export PATH="$HOME/.local/bin:$PATH"
    if command -v obsutil &> /dev/null; then
        echo "obsutil 安装成功"
    else
        echo "obsutil 安装失败，请检查 ~/.local/bin 是否在 PATH 中"
        exit 1
    fi
    cd ..
fi
obsutil config -i "${OBS_AK}" -k "${OBS_SK}" -e "obs.cn-north-4.myhuaweicloud.com"

cat > "${RESULT_JSON}" <<EOF
{
"status": "1"
}
EOF
echo "生成初始ut_result.json成功："
cat "${RESULT_JSON}"
echo "上传初始ut_result.json到OBS"
obsutil cp "${RESULT_JSON}" "obs://openjiuwen-ci/DataSwarm/ut/${PR_ID}/" --include="ut_result.json" -f -j 4

# ---------- 基础工具（系统级 shim，沿用旧脚本） ----------
pip config set global.index-url "${HW}"
pip config set global.trusted-host repo.huaweicloud.com
pip uninstall -y setuptools
pip install setuptools==68.0.0 wheel==0.41.2
pip install uv -i "${HW}"
UV_PATH=$(pip show uv | grep "Location" | awk -F ': ' '{print $2}')
export PATH="$UV_PATH/bin:$PATH"
echo "当前uv版本：$(uv --version)"

# assemble/sync 组合脚本依赖 pyyaml（CI 基础镜像未必自带）
if ! python3 -c "import yaml" &>/dev/null; then
    pip install pyyaml -i "${HW}"
fi

# ---------- 组合 build/ = 基线 tag 浅 clone + apply patch（V4 patch 自足还原） ----------
# 归档 checkout 只有 patch + 展示层，没有 jiuwenswarm/ 包，必须组装后才跑得起来；
# 这里内联 clone+apply，不依赖 assemble_build.py（该脚本仍读 trailer 的 SSH 地址，
# 目标环境无 gitcode SSH key）。
# 基线**写死常量**，与 assemble_build.py 口径一致：UT 流水线只认这两个值，
# 不再从 commit trailer 解析（trailer 记录的是归档时的追溯元信息，基线升级应改常量而非靠 trailer）。
BASELINE_REPO="https://gitcode.com/openJiuwen/jiuwenswarm.git"
BASELINE_TAG="workswarm0.2.6"
BASELINE_REPO="${ASSEMBLE_BASELINE_REPO:-$BASELINE_REPO}"
echo "[stage] 基线仓=${BASELINE_REPO} @ ${BASELINE_TAG}"
git -C "$DATASWARM_ROOT" cat-file -e "HEAD:dataswarm/patch/dataswarm.patch" 2>/dev/null || {
    echo "[错误] HEAD 树缺 dataswarm/patch/dataswarm.patch——当前检出不是归档形态？" >&2
    exit 1
}
# 每个归档 commit 的 patch 都是全量新版，复用旧 build/ 必然 apply 失败——每次重建
rm -rf "$BUILD_DIR"
# patch 从 git 对象层导出，不信任工作区字节
PATCH_TMP="$(mktemp)"
git -C "$DATASWARM_ROOT" cat-file blob "HEAD:dataswarm/patch/dataswarm.patch" > "$PATCH_TMP"
echo "[stage] patch 大小 $(wc -c < "$PATCH_TMP") bytes"
git clone --depth 1 --branch "$BASELINE_TAG" --no-checkout "$BASELINE_REPO" "$BUILD_DIR" || {
    echo "[错误] 基线 clone 失败——检查网络可达性，或用 ASSEMBLE_BASELINE_REPO=<镜像url> 覆盖" >&2
    exit 1
}
# autocrlf=false 物化：工作区须与 blob 逐字节一致（LF），否则 patch 上下文全失配
git -C "$BUILD_DIR" -c core.autocrlf=false checkout -f
git -C "$BUILD_DIR" -c core.autocrlf=false apply --check "$PATCH_TMP" || {
    echo "[错误] patch 预检失败——基线 tag 与归档记录的 Baseline-Tag 不符？" >&2
    exit 1
}
git -C "$BUILD_DIR" -c core.autocrlf=false apply "$PATCH_TMP" || {
    echo "[错误] patch 应用失败" >&2
    exit 1
}
rm -f "$PATCH_TMP"
echo "[stage] ✅ 组合完成：$BUILD_DIR = 基线 $BASELINE_TAG + patch"
[ -f "$BUILD_DIR/jiuwenswarm/__init__.py" ] || {
    echo "[错误] 组装产物异常：$BUILD_DIR/jiuwenswarm 包不存在" >&2
    exit 1
}
# 配对一致性预检：pyproject 改了 uv.lock 没跟会让 uv sync --locked 晚死——早失败早提示
echo "[stage] 配对预检（uv lock --check）..."
( cd "$DATASWARM_ROOT" && uv lock --check ) || {
    echo "[错误] 主仓 pyproject 与 uv.lock 配对不一致——先在主仓跑 uv lock 提交后再触发 UT" >&2
    exit 1
}
echo "[stage] ✅ 配对预检通过"
# 主仓两件套权威覆盖：fresh build 保留基线版 pyproject/uv.lock（无 dataswarm*
# 入口、缺 DataSwarm 依赖），覆盖后组合树与发布包同语义
cp "$DATASWARM_ROOT/pyproject.toml" "$DATASWARM_ROOT/uv.lock" "$BUILD_DIR/"
echo "[stage] ✅ 两件套已覆盖到 $BUILD_DIR/"

# 此后全部在组合树内进行（venv/sync/pytest 的相对路径都以 build 为根）——
# 漏 cd 会让 uv sync 在主仓根再建一个 .venv 并装空项目，测试全部 import 失败
cd "$BUILD_DIR"

# ---------- 虚拟环境与依赖（build 内） ----------
rm -rf "${UV_VENV_DIR}"
uv venv "${UV_VENV_DIR}"
if [ ! -f "${UV_VENV_DIR}/bin/python" ]; then
    echo "❌ 虚拟环境创建失败：${UV_VENV_DIR}/bin/python 不存在"
    exit 1
fi
echo "✅ 虚拟环境创建成功：${UV_VENV_DIR}"
source "${UV_VENV_DIR}/bin/activate"
echo "✅ 已激活虚拟环境，当前Python：$(which python)"

# 不带 --locked：lock 记录的是默认 PyPI 的 wheel URL，CI 用清华/华为镜像重解析时
# URL 不同，--locked 必误报「lockfile needs to be updated」（首跑实测踩中；
# 旧脚本不带 --locked 正是为此）。pyproject↔lock 漂移已由上方主仓侧
# uv lock --check 预检守住；此处的重锁落在 build/uv.lock，随容器丢弃
uv sync --all-extras --index-url "${TUNA}" --extra-index-url "${HW}"
if [ $? -ne 0 ]; then
    echo "❌ uv sync 依赖安装失败"
    exit 1
fi
uv pip install --upgrade pip -i "${TUNA}"
# CI 镜像 sqlite 过旧时的替换 shim（sitecustomize 兜底切换，ImportError 自动跳过）。
# pysqlite3-binary 只发 manylinux x86_64 wheel——非 Linux（本地 mac 沙箱验证等）整段跳过
if [ "$(uname -s)" = "Linux" ]; then
    uv pip install pysqlite3-binary==0.5.4.post2 --force-reinstall -i "${HW}"
fi
# 主仓 lock 未覆盖的两个插件（pytest-html/cov/asyncio 已在 test extra 内）
uv pip install pytest-xdist pytest-timeout -i "${HW}"

SITE_PACKAGES=$(python -c 'import site;print(site.getsitepackages()[0])')
export PYTHONPATH="${SITE_PACKAGES}"
cat > "${SITE_PACKAGES}/sitecustomize.py" <<'PY'
import sys
try:
 import pysqlite3
 sys.modules["sqlite3"]=pysqlite3
except ImportError:
 pass
PY

# ---------- 验证关键依赖 ----------
which python
python --version
if [ "$(uname -s)" = "Linux" ]; then
    python -c 'import pysqlite3; import sqlite3; print(f"✅ pysqlite3导入成功，sqlite3版本：{sqlite3.sqlite_version}")' || {
        echo "❌ pysqlite3导入失败！"
        pip list | grep pysqlite3
        exit 1
    }
fi
python -c 'import Crypto; from Crypto.Cipher import AES; print("✅ Crypto模块导入成功")' || {
    echo "❌ Crypto导入失败！"
    pip list | grep pycryptodome
    exit 1
}
python -c 'import jiuwenswarm; print(f"✅ jiuwenswarm 包可导入：{jiuwenswarm.__file__}")' || {
    echo "❌ jiuwenswarm 包不可导入——组合树/两件套覆盖未生效"
    exit 1
}

# ---------- 执行单元测试（build 内） ----------
# 4 个 --ignore 为存量收集债务（组合树已知清单，修复属专项）：
#   - dw_analysis_eval/ 7 例：依赖 .claude/skills 的 eval 脚本，不进 build（结构性不可收集）
#   - test_looks_like_skill_package.py：基线自带测试，delta 改写的 send_file_to_user
#     已无该公开符号
#   - test_analysis_cte_scope.py / test_plan_shape_guard.py：0be1e4f 旧稿测试，
#     import 从未定义过的符号
mkdir -p "${OUT_DIR}"
echo "[stage] pytest 开跑：$(pwd)/${TEST_DIR}（忽略 4 项存量收集债）"
set +e
python -m pytest "${TEST_DIR}" \
    --no-cov \
    -n 2 \
    --dist=worksteal \
    --max-worker-restart=0 \
    --html="${REPORT_HTML}" \
    --self-contained-html \
    --junitxml="${REPORT_XML}" \
    --timeout=60 \
    --ignore=tests/unit_tests/dw_analysis_eval \
    --ignore=tests/unit_tests/agentserver/test_looks_like_skill_package.py \
    --ignore=tests/unit_tests/dataagent/test_analysis_cte_scope.py \
    --ignore=tests/unit_tests/dataagent/test_plan_shape_guard.py
TEST_EXIT_CODE=$?
set -e

# ---------- 测试结果处理（沿用旧脚本语义） ----------
echo "测试退出码：${TEST_EXIT_CODE}"
if [ "${XML_SWITCH}" = "true" ]; then
    JUDGE=$(python - <<PYEOF || echo 1
import xml.etree.ElementTree as E
try:
    t = E.parse("${REPORT_XML}").getroot().find(".//testsuite")
    print(1 if t is None else int(t.get("failures", 0)) + int(t.get("errors", 0)))
except Exception:
    print(1)
PYEOF
)
    echo "组合判定失败用例数：${JUDGE}"
    if [ "${JUDGE}" -ne 0 ]; then TEST_EXIT_CODE=1; else TEST_EXIT_CODE=0; fi
fi

if [ -f "${REPORT_XML}" ]; then
    head -n 10 "${REPORT_XML}"
else
    echo "❌ 报告文件不存在"
    TEST_EXIT_CODE=1
fi

cat > "${RESULT_JSON}" <<EOF
{
"status": "${TEST_EXIT_CODE}"
}
EOF
echo "生成 ut_result.json 成功："
cat "${RESULT_JSON}"
echo "上传 ut_result.json 到 OBS"
obsutil cp "${RESULT_JSON}" "obs://openjiuwen-ci/DataSwarm/ut/${PR_ID}/" --include="ut_result.json" -f -j 4
obsutil cp "${OUT_DIR}" "obs://openjiuwen-ci/DataSwarm/ut/${PR_ID}/" -r -include="*.html" -include="*.xml" -f -j 4 || true

if [ "$TEST_EXIT_CODE" -ne 0 ]; then
    ut_result_value="FAILED"
    echo "##vso[task.setvariable variable=ut_result;]${ut_result_value}"
    exit 1
else
    echo "✅ 所有单元测试通过！"
fi
