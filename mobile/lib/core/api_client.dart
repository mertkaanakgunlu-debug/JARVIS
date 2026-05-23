import 'dart:async';
import 'dart:convert';
import 'package:dio/dio.dart';

class ApiClient {
  late final Dio _dio;

  ApiClient({required String host, required String apiKey}) {
    _dio = Dio(BaseOptions(
      baseUrl: 'http://$host',
      connectTimeout: const Duration(seconds: 10),
      receiveTimeout: const Duration(seconds: 60),
      headers: apiKey.isNotEmpty ? {'X-API-Key': apiKey} : {},
    ));
  }

  Future<Response<T>> get<T>(String path, {Map<String, dynamic>? params}) =>
      _dio.get<T>(path, queryParameters: params);

  Future<Response<T>> post<T>(String path, {dynamic data}) =>
      _dio.post<T>(path, data: data);

  Future<Response<T>> patch<T>(String path, {dynamic data}) =>
      _dio.patch<T>(path, data: data);

  Future<Response<T>> delete<T>(String path) =>
      _dio.delete<T>(path);

  /// Ping the PC — returns true if online within [timeout].
  Future<bool> ping({Duration timeout = const Duration(seconds: 2)}) async {
    try {
      await _dio.get(
        '/system/ping',
        options: Options(
          receiveTimeout: timeout,
          sendTimeout: timeout,
          headers: {}, // ping is auth-free
        ),
      );
      return true;
    } catch (_) {
      return false;
    }
  }

  /// Upload a file + optional query, stream SSE response from /chat/upload.
  Stream<String> uploadFileStream(
    String filePath,
    String fileName, {
    String query = '',
    String language = 'tr',
  }) async* {
    final formData = FormData.fromMap({
      'file': await MultipartFile.fromFile(filePath, filename: fileName),
      'query': query,
      'language': language,
    });
    final response = await _dio.post<ResponseBody>(
      '/chat/upload',
      data: formData,
      options: Options(
        responseType: ResponseType.stream,
        receiveTimeout: const Duration(minutes: 5),
      ),
    );
    final stream = response.data!.stream;
    final buffer = StringBuffer();
    await for (final chunk in stream) {
      buffer.write(utf8.decode(chunk, allowMalformed: true));
      final text = buffer.toString();
      final lines = text.split('\n');
      buffer.clear();
      for (int i = 0; i < lines.length - 1; i++) {
        final line = lines[i].trim();
        if (line.startsWith('data: ')) {
          yield line.substring(6);
        }
      }
      if (lines.isNotEmpty) buffer.write(lines.last);
    }
  }

  /// Stream SSE from /chat/stream — yields raw data strings.
  Stream<String> chatStream(String message, {String language = 'tr'}) async* {
    final response = await _dio.post<ResponseBody>(
      '/chat/stream',
      data: {'message': message, 'language': language},
      options: Options(responseType: ResponseType.stream),
    );
    final stream = response.data!.stream;
    final buffer = StringBuffer();
    await for (final chunk in stream) {
      buffer.write(utf8.decode(chunk, allowMalformed: true));
      final text = buffer.toString();
      final lines = text.split('\n');
      buffer.clear();
      for (int i = 0; i < lines.length - 1; i++) {
        final line = lines[i].trim();
        if (line.startsWith('data: ')) {
          yield line.substring(6);
        }
      }
      if (lines.isNotEmpty) buffer.write(lines.last);
    }
  }
}
