package io.temporal.demo.{{DOMAIN_PKG}}.activity;

import io.temporal.demo.{{DOMAIN_PKG}}.shared.TemporalIds;
import io.temporal.spring.boot.ActivityImpl;
import org.springframework.stereotype.Component;

@Component
@ActivityImpl(taskQueues = TemporalIds.ACTIVITY_TASK_QUEUE)
public class HelloActivities implements HelloActivity {

  @Override
  public String sayHello(String name) {
    return "Hello, " + name + "!";
  }
}
