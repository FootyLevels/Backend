# Owned reconstruction compatibility runtime

Copied from FootyLevels `3d-model-backend` commit
`01cff082cfff00ac323511cb81024f33717095b9`, retaining its existing API, GLB and
motion encoding. Model repositories, weights, Python environments and media tools
remain outside Git under the operator's model root. No web bundle is required for
the iOS client. This package restores the previous Debug application contract;
it does not expose or impersonate the new `api.cloud` authenticated metadata API.

Start using `infra/dgx/start_runtime.sh`, through the isolated DNS launcher.
Select exactly one GPU UUID, use one process/serial inference queue, preload body
weights, verify checkpoint hashes and preserve other users' workloads. Data is in
the operator's separate FootyLevels runtime directory, not the old checkout.
No `.env` is loaded and no paid inference provider key is inherited.

Motion releases the warm body model before running model subprocesses, avoiding
duplicate resident weights, then restores warm body inference. Optional football
and ground stages require 32 GiB free on the same selected GPU; otherwise valid
body playback is retained and the result explicitly reports partial completion.
The segmentation and whole-video models currently load per stage; this does not
claim all models are permanently resident or durable job recovery is integrated.

Firebase ownership, private Supabase file handling and Firestore leases remain
FP-14/17/53/39 integration work. Keep Release legacy transport blocked until that
boundary is complete. Large weights, generated data, logs and credentials must not
be committed.
