// clang-format off
#include <stdio.h>
#include <string.h>
#include <winsock2.h>

#pragma comment(lib, "ws2_32.lib") // Link with Winsock library

#define HTTP_BUFFER_SIZE 2048  // Buffer to accommodate headers + content

// Struct to hold network-related variables
typedef struct {
    SOCKET server_socket;
    SOCKET client_socket;
    struct sockaddr_in server_addr;
    struct sockaddr_in client_addr;
    int client_addr_len;
} SocketConnectionInfo;

typedef struct {
    SOCKET client_socket;
    int status_code;
    const char *content_type;
    const char *body;
} HttpResponse;

typedef struct Response Response;
typedef struct Response *ResponsePtr;

typedef void (*RouteHandler)(Response);

typedef struct Route{
  const char *route_key;
  RouteHandler handler;
} Route;

typedef struct Routes{
  Route routes[10];
  int route_count;
} Routes;

int HTTPServerInternal_is_socket_valid(SOCKET socket) {
    return socket != INVALID_SOCKET;
}

// Function to send response back to the client
void HTTPServerInternal_SendResponse(HttpResponse *res, const char *body, int status_code) {
    char response[HTTP_BUFFER_SIZE];
    const char *status_text;

    // Determine the status text based on status_code
    switch (status_code) {
        case 200:
            status_text = "200 OK";
            break;
        case 404:
            status_text = "404 Not Found";
            break;
        default:
            status_text = "500 Internal Server Error";
            break;
    }

    // Determine the content type automatically based on body content (basic heuristic)
    const char *content_type = "text/html";
    if (strstr(body, "<html>") != NULL) {
        content_type = "text/html";
    } else if (strstr(body, "<?xml") != NULL) {
        content_type = "application/xml";
    } else {
        content_type = "text/plain";  // Default to plain text if no HTML tags
    }

    // Prepare the complete response (headers + content)
    snprintf(response, sizeof(response),
        "HTTP/1.1 %s\r\n"
        "Content-Type: %s\r\n"
        "Connection: close\r\n"
        "\r\n"
        "%s", status_text, content_type, body);

    // Send the complete response at once
    send(res->client_socket, response, (int)strlen(response), 0);

    printf("%s \n", status_text);
}

///*///
struct Response{HttpResponse res};
namespace Response
c_function send(body : str, code : int)
    HTTPServerInternal_SendResponse(&this->res, body, code);
endc_function
endnamespace

struct HTTPServer{SocketConnectionInfo server, Routes routes};

namespace HTTPServer
c_function __init__()
    this->server.client_addr_len = sizeof(this->server.client_addr);
    this->routes.route_count = 0;
endc_function

c_function register_route(route_key : str, handler : RouteHandler)
    Route route = {route_key, handler};
    this->routes.routes[this->routes.route_count] = route;
    this->routes.route_count++;
endc_function

c_function start_server(port : int) -> int:
    WSADATA wsa_data;

    // Initialize Winsock
    if (WSAStartup(MAKEWORD(2, 2), &wsa_data) != 0) {
        printf("WSAStartup failed. Error Code: %d\n", WSAGetLastError());
        return 1;
    }

    // Create socket
    this->server.server_socket = socket(AF_INET, SOCK_STREAM, 0);
    if (this->server.server_socket == INVALID_SOCKET) {
        printf("Socket creation failed. Error Code: %d\n", WSAGetLastError());
        WSACleanup();
        return 1;
    }

    // Prepare the sockaddr_in structure
    this->server.server_addr.sin_family = AF_INET;
    this->server.server_addr.sin_addr.s_addr = INADDR_ANY;
    this->server.server_addr.sin_port = htons(port);

    // Bind the socket
    if (bind(this->server.server_socket, (struct sockaddr *)&this->server.server_addr, sizeof(this->server.server_addr)) == SOCKET_ERROR) {
        printf("Bind failed. Error Code: %d\n", WSAGetLastError());
        closesocket(this->server.server_socket);
        WSACleanup();
        return 1;
    }

    // Listen for incoming connections
    listen(this->server.server_socket, 3);

    printf("Server listening on port %d...\n", port);
    return 0;
endc_function

c_function handle_client()
    char buffer[HTTP_BUFFER_SIZE];
    int recv_size;

    // Initialize the HttpResponse object
    HttpResponse res;
    res.client_socket = this->server.client_socket;

    // Receive the HTTP request from the client
    recv_size = recv(this->server.client_socket, buffer, HTTP_BUFFER_SIZE, 0);
    if (recv_size == SOCKET_ERROR) {
        printf("Recv failed. Error Code: %d\n", WSAGetLastError());
        closesocket(this->server.client_socket);
        return;
    }
    buffer[recv_size] = '\0';  // Null-terminate the received data

    // printf("Received request:\n%s\n", buffer);

    // Print the first line of the request which contains the GET Requests.
    char first_line[256];
    int line_length = 0;

    // Find the end of the first line (before \r or \n)
    while (buffer[line_length] != '\r' && buffer[line_length] != '\n' && buffer[line_length] != '\0') {
        line_length++;
    }

    // Ensure we do not overflow the first_line buffer
    if (line_length >= sizeof(first_line)) {
        line_length = sizeof(first_line) - 1;
    }

    // Copy the first line into the smaller buffer
    memcpy(first_line, buffer, line_length);
    first_line[line_length] = '\0'; // Null-terminate the copied string

    // Print the first line
    printf("%s\n", first_line);    

    Response resp = {res};

    int route_found = 0;
    for(int i = 0; i < this->routes.route_count; i++) {
        if (strstr(buffer, this->routes.routes[i].route_key) == buffer) {
            this->routes.routes[i].handler(resp);
            route_found = 1;
            break;
        }
    }
    if (!route_found) {
        char* html = "<html><body><h1>404 Page Not Found</h1></body></html>\r\n";
        Responsesend(&resp, html, 404);
    }

    // Close the connection with the client
    closesocket(this->server.client_socket);
endc_function

c_function accept_connections()
    while (1) {
        this->server.client_socket = accept(this->server.server_socket, (struct sockaddr *)&this->server.client_addr, &this->server.client_addr_len);
        if (!HTTPServerInternal_is_socket_valid(this->server.client_socket)) {
            printf("Accept failed. Error Code: %d\n", WSAGetLastError());
            break;
        }

        printf("Client connected!\n");
        HTTPServerhandle_client(this);
    }
endc_function

function listen(port : int)
    if this.start_server(port) == 0 {
        this.accept_connections()
    }
endfunction

c_function __del__()
    closesocket(this->server.server_socket);
    WSACleanup();
endc_function
endnamespace
///*///