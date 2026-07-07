package io.temporal.demo.{{DOMAIN_PKG}}.activity;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class {{Domain}}ActivityWorkerApplication {

  public static void main(String[] args) {
    SpringApplication.run({{Domain}}ActivityWorkerApplication.class, args);
  }
}
