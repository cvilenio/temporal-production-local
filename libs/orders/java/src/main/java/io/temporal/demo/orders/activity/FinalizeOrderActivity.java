package io.temporal.demo.orders.activity;

import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;
import orders.activities.v1.Activities.FinalizeOrderRequest;

@ActivityInterface
public interface FinalizeOrderActivity {

  @ActivityMethod(name = "finalize_order")
  void finalizeOrder(FinalizeOrderRequest req);
}
