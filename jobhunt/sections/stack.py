"""A code project's stack, read from its manifests with no AI: each direct dependency named as a tool.

`read(folder)` finds the manifests at the top of a project (and one level down, where a project keeps its
app in `backend/` or `web/`) and gives each dependency it declares as a `Dep`: the tool's name, the words
as the manifest writes them, and the file and line, so every tool on a project card is quoted.

Design decisions, each with its reason:

- Only direct dependencies count: what the project's own manifest lists, never a lockfile, so a tool that
  only arrived as a dependency of a dependency is never told as one the person used.
- `TOOLS` maps the common packages of each ecosystem to the names people write on a CV (torch is PyTorch,
  react-dom is React). A package not in it keeps its own name: guessing a name would be the AI adding
  tools, which the plan forbids (sections plan, 2026-11-09).
- Files that are a tool by themselves (a Dockerfile, a Godot or Unity project) are listed in `FILE_TOOLS`;
  their quote is the file's first line. A Dockerfile counts however deep it is and when named for its image
  (`Dockerfile.box`, `app.dockerfile`), since a project with packages keeps one beside each: Docker was told
  as a gap for a project that runs its agent in a Docker box (eval, 2026-11-12).
- Every manifest is read with a size cap (`MANIFEST_MAX`) and a broken one gives no tools, never an error:
  one unreadable file must not stop the rest of the project being read.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tomllib
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

MANIFEST_MAX = 512 * 1024      # a manifest is a few KiB; a bigger file is generated or data
DEPTH = 1                      # manifests are looked for at the top and one folder down
SKIP_DIRS = {"node_modules", "vendor", "third_party", "third-party", "external", ".git", "dist", "build",
             ".venv", "venv", "env", "target", "__pycache__", "site-packages", "Library", "Packages"}

# Common packages and the tool names people write (lower-case package name -> tool). The package's own name
# is kept for anything not here.
TOOLS: dict[str, str] = {
    # Python
    "torch": "PyTorch", "pytorch": "PyTorch", "torchvision": "PyTorch", "tensorflow": "TensorFlow",
    "keras": "Keras", "jax": "JAX", "scikit-learn": "scikit-learn", "sklearn": "scikit-learn",
    "numpy": "NumPy", "pandas": "pandas", "polars": "Polars", "scipy": "SciPy", "matplotlib": "Matplotlib",
    "seaborn": "seaborn", "plotly": "Plotly", "statsmodels": "statsmodels", "xgboost": "XGBoost",
    "lightgbm": "LightGBM", "transformers": "Hugging Face Transformers", "datasets": "Hugging Face Datasets",
    "langchain": "LangChain", "openai": "OpenAI API", "anthropic": "Anthropic API", "opencv-python": "OpenCV",
    "opencv-python-headless": "OpenCV", "pillow": "Pillow", "nltk": "NLTK", "spacy": "spaCy",
    "fastapi": "FastAPI", "flask": "Flask", "django": "Django", "djangorestframework": "Django REST framework",
    "starlette": "Starlette", "uvicorn": "Uvicorn", "gunicorn": "Gunicorn", "sqlalchemy": "SQLAlchemy",
    "pydantic": "Pydantic", "celery": "Celery", "requests": "Requests", "httpx": "HTTPX", "aiohttp": "aiohttp",
    "beautifulsoup4": "Beautiful Soup", "scrapy": "Scrapy", "selenium": "Selenium", "playwright": "Playwright",
    "pytest": "pytest", "streamlit": "Streamlit", "dash": "Dash", "jupyter": "Jupyter", "notebook": "Jupyter",
    "jupyterlab": "Jupyter", "psycopg2": "PostgreSQL", "psycopg2-binary": "PostgreSQL", "psycopg": "PostgreSQL",
    "pymongo": "MongoDB", "redis": "Redis", "boto3": "AWS", "google-cloud-storage": "Google Cloud",
    "pygame": "Pygame", "tkinter": "Tkinter", "pyqt5": "Qt", "pyqt6": "Qt", "pyside6": "Qt",
    "networkx": "NetworkX", "sympy": "SymPy", "biopython": "Biopython", "rdkit": "RDKit", "astropy": "Astropy",
    # JavaScript and TypeScript
    "react": "React", "react-dom": "React", "next": "Next.js", "vue": "Vue", "nuxt": "Nuxt", "svelte": "Svelte",
    "@sveltejs/kit": "SvelteKit", "@angular/core": "Angular", "preact": "Preact", "express": "Express",
    "koa": "Koa", "fastify": "Fastify", "@nestjs/core": "NestJS", "typescript": "TypeScript", "vite": "Vite",
    "webpack": "webpack", "jest": "Jest", "vitest": "Vitest", "mocha": "Mocha", "cypress": "Cypress",
    "@playwright/test": "Playwright", "tailwindcss": "Tailwind CSS", "bootstrap": "Bootstrap", "d3": "D3.js",
    "three": "three.js", "chart.js": "Chart.js", "redux": "Redux", "@reduxjs/toolkit": "Redux",
    "graphql": "GraphQL", "@apollo/client": "Apollo", "mongoose": "MongoDB", "pg": "PostgreSQL",
    "prisma": "Prisma", "@prisma/client": "Prisma", "sequelize": "Sequelize", "socket.io": "Socket.IO",
    "electron": "Electron", "react-native": "React Native", "expo": "Expo", "jquery": "jQuery",
    "firebase": "Firebase", "@supabase/supabase-js": "Supabase", "eslint": "ESLint", "prettier": "Prettier",
    "sass": "Sass", "p5": "p5.js",
    # Rust, Go, Ruby, PHP, Java and .NET
    "tokio": "Tokio", "serde": "Serde", "actix-web": "Actix Web", "axum": "Axum", "rocket": "Rocket",
    "bevy": "Bevy", "clap": "clap", "github.com/gin-gonic/gin": "Gin", "github.com/gorilla/mux": "Gorilla",
    "github.com/labstack/echo/v4": "Echo", "gorm.io/gorm": "GORM", "rails": "Ruby on Rails", "sinatra": "Sinatra",
    "rspec": "RSpec", "laravel/framework": "Laravel", "symfony/symfony": "Symfony", "phpunit/phpunit": "PHPUnit",
    "spring-boot-starter-web": "Spring Boot", "spring-boot-starter": "Spring Boot", "junit": "JUnit",
    "junit-jupiter": "JUnit", "hibernate-core": "Hibernate", "lombok": "Lombok",
    "microsoft.aspnetcore.app": "ASP.NET Core", "entityframework": "Entity Framework",
    "microsoft.entityframeworkcore": "Entity Framework", "xunit": "xUnit", "nunit": "NUnit",
    "newtonsoft.json": "Json.NET",
    # R and Julia
    "ggplot2": "ggplot2", "dplyr": "dplyr", "tidyverse": "tidyverse", "shiny": "Shiny", "data.table": "data.table",
    "rmarkdown": "R Markdown", "knitr": "knitr", "plots": "Plots.jl", "dataframes": "DataFrames.jl",
    "flux": "Flux.jl",
}
# Files that are a tool themselves (lower-case name -> tool).
FILE_TOOLS = {"docker-compose.yml": "Docker Compose", "docker-compose.yaml": "Docker Compose",
              "compose.yml": "Docker Compose", "compose.yaml": "Docker Compose", "project.godot": "Godot",
              "cmakelists.txt": "CMake", "makefile": "Make", "terraform.tf": "Terraform", "main.tf": "Terraform"}
FILE_SUFFIX_TOOLS = {".prj": "MATLAB", ".mlx": "MATLAB", ".slx": "Simulink", ".uproject": "Unreal Engine",
                     ".kicad_pro": "KiCad", ".ino": "Arduino"}
UNITY_MARKER = "ProjectSettings/ProjectVersion.txt"
REQUIREMENT = re.compile(r"^\s*(@?[A-Za-z0-9][A-Za-z0-9._\-\[\]/]*)")     # a name, a scoped npm name or a module path
NOT_PACKAGES = {"python", "pip", "setuptools", "wheel", "php", "r", "julia"}


@dataclass(frozen=True)
class Dep:
    """One direct dependency: the tool's name, the manifest's words for it, and where they are."""

    tool: str
    said: str
    file: str
    line: int


def tool_name(package: str) -> str:
    """The tool a package is known as: `TOOLS`' name, else the package's own name without extras."""
    bare = re.sub(r"\[.*\]$", "", package.strip())
    return TOOLS.get(bare.lower(), bare)


def _text(path: Path) -> str:
    try:
        if path.stat().st_size > MANIFEST_MAX:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _line_of(text: str, words: str) -> int:
    """The first line (from 1) holding `words`, or 1 when it is not found as such (a table key)."""
    for n, line in enumerate(text.splitlines(), 1):
        if words in line:
            return n
    return 1


def _pep508(spec: str) -> str:
    m = REQUIREMENT.match(spec)
    return m.group(1) if m else ""


def _found(text: str, rel: str, packages: list[str]) -> list[Dep]:
    out = []
    for package in packages:
        name = _pep508(package)
        if not name or name.lower() in NOT_PACKAGES or name.lower().startswith("ext-"):
            continue
        line = _line_of(text, name)
        said = (text.splitlines()[line - 1:line] or [name])[0].strip().rstrip(",")
        out.append(Dep(tool_name(name), said if name in said else name, rel, line))
    return out


def _toml(text: str) -> dict:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}


def _pyproject(text: str) -> list[str]:
    data = _toml(text)
    found = [str(d) for d in (data.get("project") or {}).get("dependencies") or []]
    poetry = ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
    return found + [str(k) for k in poetry]


def _requirements(text: str) -> list[str]:
    return [ln.split("#", 1)[0].strip() for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith(("#", "-"))]


def _setup_cfg(text: str) -> list[str]:
    """The indented lines under `install_requires =`."""
    out, inside = [], False
    for ln in text.splitlines():
        if re.match(r"install_requires\s*=", ln):
            inside = True
            first = ln.split("=", 1)[1].strip()
            out += [first] if first else []
        elif inside and ln[:1] in (" ", "\t") and ln.strip():
            out.append(ln.strip())
        elif inside:
            break
    return out


def _package_json(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    return [k for key in ("dependencies", "devDependencies") for k in (data.get(key) or {})
            if isinstance(data.get(key), dict)]


def _cargo(text: str) -> list[str]:
    return list(_toml(text).get("dependencies") or {})


def _julia(text: str) -> list[str]:
    return list(_toml(text).get("deps") or {})


def _go_mod(text: str) -> list[str]:
    out = []
    block = False
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("require ("):
            block = True
            continue
        if block and s == ")":
            block = False
            continue
        if "// indirect" in s:
            continue
        if block and s:
            out.append(s.split()[0])
        elif s.startswith("require ") and not s.endswith("("):
            out.append(s.split()[1])
    return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _pom(text: str) -> list[str]:
    """The `artifactId` of each `dependency` in a Maven `pom.xml`."""
    try:
        # the stdlib parser never fetches external entities and its expat (2.4.1 and later) caps entity
        # expansion, so an untrusted pom cannot reach the network or blow up (Python docs, "XML
        # vulnerabilities", checked 2026-11-08); the size cap (`MANIFEST_MAX`) bounds the rest
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    return [a.text.strip() for el in root.iter() if _local(el.tag) == "dependency"
            for a in el if _local(a.tag) == "artifactId" and a.text]


def _gradle(text: str) -> list[str]:
    found = re.findall(r"^\s*(?:implementation|api|compileOnly|runtimeOnly|testImplementation)\s*\(?\s*['\"]"
                       r"[^:'\"]+:([^:'\"]+)", text, re.M)
    return found


def _gemfile(text: str) -> list[str]:
    return re.findall(r"^\s*gem\s+['\"]([^'\"]+)['\"]", text, re.M)


def _composer(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    req = data.get("require") if isinstance(data, dict) else None
    return [k for k in req or {} if "/" in k] if isinstance(req, dict) else []


def _csproj(text: str) -> list[str]:
    return re.findall(r"<PackageReference\s+Include=\"([^\"]+)\"", text)


def _environment(text: str) -> list[str]:
    out = []
    for ln in text.splitlines():
        m = re.match(r"^\s+-\s+([A-Za-z0-9][A-Za-z0-9._\-]*)", ln)
        if m and m.group(1) != "pip":
            out.append(m.group(1))
    return out


def _description(text: str) -> list[str]:
    """The packages of an R `DESCRIPTION`'s `Imports` and `Depends` fields, which run on over indented lines."""
    out, inside = [], False
    for ln in text.splitlines():
        field_name, colon, rest = ln.partition(":")
        if colon and not ln[:1].isspace():
            inside = field_name in ("Imports", "Depends")
            words = rest if inside else ""
        elif inside and ln[:1].isspace():
            words = ln
        else:
            continue
        out += [p.split("(")[0].strip() for p in words.split(",") if p.strip()]
    return out


# File name (lower case) or glob -> the reader of its dependency list.
MANIFESTS = (("pyproject.toml", _pyproject), ("requirements*.txt", _requirements), ("setup.cfg", _setup_cfg),
             ("package.json", _package_json), ("cargo.toml", _cargo), ("go.mod", _go_mod), ("pom.xml", _pom),
             ("build.gradle", _gradle), ("build.gradle.kts", _gradle), ("gemfile", _gemfile),
             ("composer.json", _composer), ("*.csproj", _csproj), ("environment.yml", _environment),
             ("environment.yaml", _environment), ("description", _description), ("project.toml", _julia))


def _folders(folder: Path) -> Iterator[Path]:
    yield folder
    if DEPTH:
        try:
            subs = sorted(p for p in folder.iterdir() if p.is_dir() and not p.is_symlink())
        except OSError as e:      # an unreadable folder has no manifests to give; the top one was read
            log.debug("could not list %s: %s", folder, e)
            return
        yield from (p for p in subs if p.name not in SKIP_DIRS and not p.name.startswith("."))


def _is_dockerfile(lower: str) -> bool:
    return lower == "dockerfile" or lower.startswith("dockerfile.") or lower.endswith(".dockerfile")


def _first_line(path: Path) -> str:
    return next((ln.strip() for ln in _text(path).splitlines() if ln.strip()), path.name)[:120]


def _deep_dockerfile(folder: Path) -> Path | None:
    """The first Dockerfile anywhere in the project, outside skipped and hidden folders."""
    for here, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(names):
            if _is_dockerfile(name.lower()) and not Path(here, name).is_symlink():
                return Path(here, name)
    return None


def read(folder: Path) -> list[Dep]:
    """Every direct dependency the project's manifests declare, and the files that are tools, as `Dep`s."""
    out: list[Dep] = []
    for here in _folders(folder):
        try:
            names = sorted(p for p in here.iterdir() if p.is_file() and not p.is_symlink())
        except OSError as e:      # one unreadable folder must not stop the others being read
            log.debug("could not list %s: %s", here, e)
            continue
        for path in names:
            lower = path.name.lower()
            rel = path.relative_to(folder).as_posix()
            for pattern, parse in MANIFESTS:
                if Path(lower).match(pattern) and not (pattern == "description" and here != folder):
                    text = _text(path)
                    out += _found(text, rel, parse(text)) if text else []
            tool = ("Docker" if _is_dockerfile(lower) else
                    FILE_TOOLS.get(lower) or FILE_SUFFIX_TOOLS.get(path.suffix.lower()))
            if tool:
                out.append(Dep(tool, _first_line(path), rel, 1))
    deep = None if any(d.tool == "Docker" for d in out) else _deep_dockerfile(folder)
    if deep:
        out.append(Dep("Docker", _first_line(deep), deep.relative_to(folder).as_posix(), 1))
    if (folder / UNITY_MARKER).is_file():
        first = next(iter(_text(folder / UNITY_MARKER).strip().splitlines()), "Unity")
        out.append(Dep("Unity", first[:120], UNITY_MARKER, 1))
    seen: set[str] = set()
    unique = []
    for dep in out:
        if dep.tool.lower() not in seen:
            seen.add(dep.tool.lower())
            unique.append(dep)
    return unique
