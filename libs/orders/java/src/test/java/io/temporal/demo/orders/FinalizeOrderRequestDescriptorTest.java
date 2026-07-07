package io.temporal.demo.orders;

import static org.junit.jupiter.api.Assertions.assertEquals;

import orders.activities.v1.Activities.FinalizeOrderRequest;
import org.junit.jupiter.api.Test;

class FinalizeOrderRequestDescriptorTest {

  @Test
  void descriptorFullNameMatchesWireContract() {
    assertEquals(
        "orders.activities.v1.FinalizeOrderRequest",
        FinalizeOrderRequest.getDescriptor().getFullName());
  }
}
