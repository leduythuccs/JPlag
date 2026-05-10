# JPlag MOSS Server

A MOSS-compatible TCP server that accepts submissions via the MOSS protocol, runs [JPlag](https://github.com/jplag/JPlag) for plagiarism detection, and returns a URL to view the results in JPlag's web report viewer.

## Prerequisites

**Python 3.10+**

**Java SE 25** (required by JPlag)

### 1. Build the JPlag JAR

From the repository root:

```bash
mvn clean package assembly:single
# JAR will be at: cli/target/jplag-6.3.0-jar-with-dependencies.jar
```

### 2. Build the report viewer

```bash
cd report-viewer/report-viewer
npm install
npm run build
# Output at: report-viewer/report-viewer/dist/
```

## Running

From the `moss-server/` directory:

```bash
python server.py
```

Both the MOSS TCP server (`:7690`) and the HTTP viewer server (`:8080`) start in the same process.

## Options

| Flag | Default | Description |
|---|---|---|
| `--bind` | `0.0.0.0` | Bind address for the MOSS TCP server |
| `--public-host` | `localhost` | Hostname used in returned result URLs |
| `--moss-port` | `7690` | MOSS protocol TCP port |
| `--http-port` | `8080` | HTTP port for the viewer and result files |
| `--jplag-jar` | `../cli/target/jplag-6.3.0-jar-with-dependencies.jar` | Path to the JPlag JAR |
| `--viewer-dir` | `../report-viewer/report-viewer/dist` | Path to the built report viewer `dist/` |
| `--work-dir` | `/tmp/jplag-moss-sessions` | Directory for session files and results |
| `--java` | `java` | Java executable |
| `--public-port` | *(same as `--http-port`)* | Port in returned URLs — set to `80`/`443` when behind nginx |
| `--public-scheme` | `http` | Scheme in returned URLs: `http` or `https` |
| `--allowed-users` | *(all allowed)* | Comma-separated whitelist of MOSS user IDs |
| `--log-level` | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### Example

```bash
python server.py \
  --public-host plagiarism.example.com \
  --moss-port 7690 \
  --http-port 8080 \
  --allowed-users 123456,789012
```

## How It Works

1. A MOSS client connects on the TCP port and sends submissions using the MOSS protocol.
2. The server reconstructs the submission directory layout from the file display names (e.g. `student1/main.cpp`).
3. JPlag is run in `RUN` mode, producing a `.jplag` result file.
4. The result file is published under `<work-dir>/results/`.
5. The server returns a URL: `http://<public-host>:<http-port>/?file=/results/<id>.jplag`
6. Opening the URL loads JPlag's report viewer, which fetches and displays the result.

## Language Mapping

| MOSS language | JPlag language |
|---|---|
| `c` | `c` |
| `cc` | `cpp` |
| `java` | `java` |
| `python` | `python3` |
| `csharp` | `csharp` |
| `javascript` | `javascript` |
| `scheme` | `scheme` |
| *(all others)* | `text` |

## Deploying with nginx

The Python process runs internally; nginx handles the public HTTP port.
The MOSS TCP port (`7690`) is raw TCP and is exposed directly — nginx is not needed for it.

```bash
python server.py \
  --bind 127.0.0.1 \       # HTTP server: internal only
  --http-port 8080 \        # Python binds here
  --public-port 80 \        # port used in returned URLs (matches nginx)
  --public-host your-domain.com \
  --moss-port 7690 \        # MOSS TCP: keep exposed directly
  --allowed-users 123456
```

> `--public-port` sets the port embedded in result URLs without affecting what port Python listens on.
> Without it, the URL would contain `:8080` which wouldn't be reachable through nginx.

## Using with the MOSS Client

The server is moss-compatible, so it's expected to work with any moss-client

That's being said, only [moss.py](https://github.com/soachishti/moss.py) was tested.
