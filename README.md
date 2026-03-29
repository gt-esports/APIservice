# Esports API Platform

## Overview

This project is a centralized API platform that sits in front of multiple club applications (e.g., GameFest, Hackathon). It introduces an API Gateway layer to provide a unified backend interface instead of having each application directly interact with the database.

The platform standardizes authentication, routing, and shared infrastructure concerns, while allowing each domain (GameFest, Hackathon, etc.) to maintain its own logic and database schema.

---

## Architecture Doc

https://docs.google.com/document/d/1prQqau4Jkh9AYgPkPXGmu8ELvTry273Ot8y310UXGd0/edit

---

## Design Principles

- **Centralized Authentication**  
  Authentication is handled once at the gateway using JWT, instead of being re-implemented across services.

- **Service Isolation**  
  Each domain service owns its own API, business logic, and database schema.

- **Decoupling Clients from Data**  
  Client applications interact only with APIs, not directly with the database.

- **Gateway as Policy Layer**  
  The gateway enforces cross-cutting concerns such as authentication, logging, and rate limiting.

- **Minimal Shared Code**  
  Shared modules are limited to infrastructure utilities (auth helpers, logging, types), with no business logic.

