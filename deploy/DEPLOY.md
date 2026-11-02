# Deploying Saarthi 2.0 to the Hostinger VPS

**Live: https://indux5.0.visioncoders.in** (deployed 13 Oct 2026)

```
DNS      indux5.0.visioncoders.in  A  187.127.191.13  TTL 300
ingress  Traefik (shared)  ->  127.0.0.1:8092  ->  container indux-saarthi
stack    /opt/stacks/indux-saarthi/
route    /opt/stacks/traefik/dynamic/indux-saarthi.yml
image    saarthi2:local
volume   indux-saarthi_saarthi2-storage  (sqlite, vector index, cached TTS audio)
cert     Let's Encrypt, issued 13 Oct 2026, renews automatically
```

Port **8092** and not 8010: the local dev port is taken on this box by
`soilq-precision`. In use when this was deployed: 3010, 8001, 8003, 8010, 8085,
8086, 8087, 8090, 8100, 8111, 32768.

---

## Two models travel with the image

`data/models/` holds both, baked into the image like the RAG corpus, because
they are versioned with the code that loads them:

| file | size | what loads it |
|---|---|---|
| `leaf_disease.onnx` | 16 MB | `app/vision/classifier.py` |
| `speaker_ecapa.onnx` | 84 MB | `app/core/speakermodel.py` |

The speaker model is ECAPA-TDNN (`speechbrain/spkrec-ecapa-voxceleb`), exported
by `ml/export_speaker_model.py`. It takes the tarball from about 25 MB to about
110 MB, which is the whole cost of the change - the server already had
`onnxruntime` and needs nothing else, and the graph is loaded lazily, so a
container where nobody uses voice ID stays at its usual ~125 MB of memory.

**If it is missing from the image, voice matching still works** - it falls back
to the cepstral description, says so in the log, and `/api/voiceid/status`
reports `"describer": "cepstral"`. That is the thing to check after a deploy:

```bash
curl -s https://indux5.0.visioncoders.in/api/voiceid/status  # needs a session
docker logs indux-saarthi 2>&1 | grep -i "speaker model"
```

Every farmer registered before this change has a print made by the old
description. Those are reported `"stale": true` and the dashboard asks them to
register again, rather than comparing numbers that mean different things.

## The crop-disease model travels with the image

`data/models/leaf_disease.onnx` (~16 MB) and its model card are **baked into the image**,
like the RAG corpus - they are versioned with the code that loads them. The server runs
them with `onnxruntime` on CPU; PyTorch is never installed there.

So a model retrain is just a redeploy: run `ml/export_onnx.py` on the workstation, then
follow the steps below. Check it landed with:

```bash
curl -s https://indux5.0.visioncoders.in/api/vision/status | python3 -m json.tool | head -20
```

If that returns `"available": false`, the image was built without `data/models` - check
that `.dockerignore` still excludes only `data/storage`, not all of `data/`.

## Redeploy after a code change

From the project root on Windows:

```bash
tar --exclude='./.venv' --exclude='./.venv-train' --exclude='./.git' \
    --exclude='./data/storage' --exclude='./ml/artifacts' --exclude='./ml/runs' \
    --exclude='*/__pycache__' --exclude='*.pyc' --exclude='./backend/.pytest_cache' \
    --exclude='./config/api_keys.env' \
    -czf /tmp/saarthi2.tar.gz .
scp -i ~/.ssh/nexus_vps /tmp/saarthi2.tar.gz root@187.127.191.13:/tmp/
ssh -i ~/.ssh/nexus_vps root@187.127.191.13 '
  cd /opt/stacks/indux-saarthi
  tar -xzf /tmp/saarthi2.tar.gz -C .
  chown 10001:10001 config/api_keys.env && chmod 600 config/api_keys.env
  docker compose build && docker compose up -d
  docker ps --filter name=indux-saarthi --format "{{.Status}}"'
```

`.venv-train` is the 4.5 GB CUDA training environment and `ml/artifacts` holds the
training checkpoints - leaving either out of the excludes produces a ~2.9 GB tarball
instead of ~25 MB. `config/api_keys.env` is excluded on purpose: it is a single-FILE
bind mount, and extracting over it replaces the inode, which is exactly the failure
described under "Changing a key" below. Add a new setting with an in-place append:

```bash
ssh -i ~/.ssh/nexus_vps root@187.127.191.13 '
  cd /opt/stacks/indux-saarthi
  grep -q "^VOICE_ANSWER_MODEL=" config/api_keys.env \
    || echo "VOICE_ANSWER_MODEL=ministral-8b-latest" >> config/api_keys.env
  docker compose restart'
```

The application code is **baked into the image**, so editing a file on the host and
running `docker compose restart` changes nothing - it needs `build` then `up -d`.

Verify the upload arrived intact rather than trusting scp's exit code:

```bash
sha256sum /tmp/saarthi2.tar.gz                                   # local
ssh -i ~/.ssh/nexus_vps root@187.127.191.13 'sha256sum /tmp/saarthi2.tar.gz'
```

---

## Changing a key

```bash
ssh -i ~/.ssh/nexus_vps root@187.127.191.13
cd /opt/stacks/indux-saarthi
nano config/api_keys.env        # edit IN PLACE
docker compose restart          # the file is read at startup
```

**Never `mv` a new file over `config/api_keys.env`.** It is a single-FILE bind mount, so
Docker binds the inode: a replaced file leaves the container reading the old one forever
while the host shows the new contents. Only `docker compose up -d --force-recreate`
re-resolves it. Editing in place (`nano`, `cat > file`) is safe.

The file must stay `chown 10001:10001`, `chmod 600` - the container runs as uid 10001,
and a root-owned 600 file reads as "no keys configured" with no error.

---

## Operations

```bash
docker logs -f indux-saarthi                       # application log
docker compose ps                                  # health
curl -s https://indux5.0.visioncoders.in/api/health
curl -s https://indux5.0.visioncoders.in/api/keys/status   # what is live, what is not
docker exec indux-saarthi python -c "import sys;print(sys.version)"
```

Storage lives in the named volume, so `docker compose down && up -d` keeps the sessions,
advisories, sensor history and call log. To wipe them deliberately:
`docker volume rm indux-saarthi_saarthi2-storage`.

---

## Traefik notes (shared ingress - be careful)

* The route is a **file-provider** config, one router for the one host. Never list two
  hosts on one router: Traefik then asks for a single bundled SAN certificate, and if any
  host in it lacks DNS the whole certificate fails *and the failure is cached*.
* The file provider reloads on **content** change. `touch` does nothing.
* **Do not restart Traefik** to pick up a route - it is shared with nexus, n8n, agritech,
  soilq, sentinel and the rest, and a restart interrupts all of them.
* Adding a second hostname later means adding a second router block in the same file,
  pointing at the same service.

## Gotchas specific to this box

* **SSH times out intermittently.** Retry two or three times; it is not an outage. ICMP is
  blocked, so ping is not a health signal.
* **Verify from the VPS, not from the office network.** A web-filtering appliance there
  intercepts some requests to these hosts and returns its own 403 page, which looks exactly
  like a broken deployment.
* DNS must be checked with `getent hosts <name>` **on the VPS**; the Windows resolver
  reports timeouts as NXDOMAIN, which looks like a missing record.
