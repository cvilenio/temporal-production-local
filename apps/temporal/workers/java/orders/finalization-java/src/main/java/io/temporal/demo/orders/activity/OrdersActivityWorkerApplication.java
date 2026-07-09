package io.temporal.demo.orders.activity;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class OrdersActivityWorkerApplication {

  public static void main(String[] args) {
    SpringApplication.run(OrdersActivityWorkerApplication.class, args);
  }
}
