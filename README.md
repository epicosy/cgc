# cgc
Cyber Grand Challenge Corpus benchmark plugin for Orbis

#### 1) Configure cores path in the host environment
The default core dump output folder is `/cores`, and is set in the `init.sh` script.
The output files must have the following pattern ```core.hostname.pid.path```.
You can change the core dump location by substituting the `/cores` path in the following command:

> **Note**: make sure this change is also made in the configurations.

```
echo '/cores/core.%h.%p.%E' | sudo tee /proc/sys/kernel/core_pattern
```

