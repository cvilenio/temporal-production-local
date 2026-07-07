package io.temporal.demo.{{DOMAIN_PKG}}.activity;

import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;
import io.temporal.demo.{{DOMAIN_PKG}}.shared.TemporalIds;

@ActivityInterface
public interface HelloActivity {

  @ActivityMethod(name = TemporalIds.SAY_HELLO_ACTIVITY)
  String sayHello(String name);
}
