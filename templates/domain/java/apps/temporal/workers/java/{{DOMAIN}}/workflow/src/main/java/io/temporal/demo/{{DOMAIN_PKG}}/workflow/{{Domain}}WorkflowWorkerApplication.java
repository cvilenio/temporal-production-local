package io.temporal.demo.{{DOMAIN_PKG}}.workflow;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class {{Domain}}WorkflowWorkerApplication {

  public static void main(String[] args) {
    SpringApplication.run({{Domain}}WorkflowWorkerApplication.class, args);
  }
}
