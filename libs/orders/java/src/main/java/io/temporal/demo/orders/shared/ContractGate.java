package io.temporal.demo.orders.shared;

import io.temporal.failure.ApplicationFailure;
import orders.activities.v1.Activities.FinalizeOrderRequest;

/** Activity-side version-in-command guard (ADR-0021) — mirrors Python {@code contract_gate.gate}. */
public final class ContractGate {

  public static final int CONTRACT_VERSION = 1;

  private ContractGate() {}

  public static int gate(FinalizeOrderRequest req) {
    int version = req.getContractVersion() == 0 ? 1 : req.getContractVersion();
    if (version < 1 || version > CONTRACT_VERSION) {
      throw ApplicationFailure.newNonRetryableFailure(
          "unsupported contract_version "
              + version
              + " (supported 1.."
              + CONTRACT_VERSION
              + ")",
          "ContractVersionUnsupported");
    }
    return version;
  }
}
